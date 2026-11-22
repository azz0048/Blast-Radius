"""Bounded, isolated executions of tests on the CURRENT runtime patch.

No patch answers are stored. A fork starts each trial from an unmodified
interpreter snapshot; changed modules and resources are supplied in memory.
"""
from __future__ import annotations
import builtins
import concurrent.futures
import gzip
import importlib.abc
import importlib.util
import io
import json
import os
from pathlib import Path
import pickle
import select
import signal
import sys
import time
from test_execution import run_tests

class MemoryLoader(importlib.abc.Loader):
    def __init__(self,path,content):self.path,self.content=path,content
    def create_module(self,spec):return None
    def exec_module(self,module):exec(compile(self.content,self.path,'exec'),module.__dict__)
    def get_source(self,fullname):return self.content.decode('utf-8')
    def get_filename(self,fullname):return self.path

class PatchFinder(importlib.abc.MetaPathFinder):
    def __init__(self,root,changes):
        self.modules={}
        for path,content in changes.items():
            if path.endswith('.py'):
                name=path[:-3].replace('/','.')
                package=name.endswith('.__init__')
                if package:name=name[:-9]
                self.modules[name]=(str(root/path),content,package)
    def find_spec(self,fullname,path=None,target=None):
        if fullname not in self.modules:return None
        filename,content,package=self.modules[fullname]
        return importlib.util.spec_from_file_location(fullname,filename,loader=MemoryLoader(filename,content),submodule_search_locations=[str(Path(filename).parent)] if package else None)

class NamedBytesIO(io.BytesIO):
    def __init__(self,data,name):super().__init__(data);self.name=name
class NamedStringIO(io.StringIO):
    def __init__(self,data,name,newline=None):super().__init__(data,newline=newline);self.name=name

class Prober:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.ready=False
    def prepare(self):
        if self.ready:return
        # Heavy extension imports are shared with children using copy-on-write.
        os.environ['OPENBLAS_NUM_THREADS']='1'
        os.environ['OMP_NUM_THREADS']='1'
        os.environ['SKIP_INTEGRATION']='0'
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
        os.environ['PYTHONDONTWRITEBYTECODE']='1'
        import pytest, pandas, numpy, duckdb
        original_connect=duckdb.connect
        def bounded_connect(*args,**kwargs):
            config=dict(kwargs.pop('config',{}) or {})
            config.setdefault('threads','1')
            config.setdefault('memory_limit','512MB')
            kwargs['config']=config
            return original_connect(*args,**kwargs)
        duckdb.connect=bounded_connect
        self.ready=True
    def run(self,changes,test_ids,seconds=12,case_timeout=4,max_rss_mb=1536):
        self.prepare()
        read_fd,write_fd=os.pipe()
        sys.stdout.flush();sys.stderr.flush()
        pid=os.fork()
        if pid==0:
            os.close(read_fd)
            def emit(kind,payload):
                data=json.dumps({'kind':kind,'result':payload},separators=(',',':')).encode()+b'\n'
                while data:
                    written=os.write(write_fd,data);data=data[written:]
            try:
                os.setsid()
                with open(os.devnull,'w') as sink:
                    os.dup2(sink.fileno(),1)
                    if not os.environ.get('BR_PROBE_DEBUG'):os.dup2(sink.fileno(),2)
                result=self._child(changes,test_ids,lambda update:emit('progress',update),case_timeout=case_timeout)
            except BaseException as error:
                result={'complete':False,'error':type(error).__name__}
            try:
                emit('final',result)
            finally:os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        deadline=time.monotonic()+seconds;buffer=b'';finished=False;final=None
        observed={key:set() for key in ('checked','failed','import_errors','setup_errors')}
        def consume(data):
            nonlocal buffer,final
            buffer+=data
            while b'\n' in buffer:
                line,buffer=buffer.split(b'\n',1)
                try:event=json.loads(line)
                except (ValueError,UnicodeError):continue
                payload=event.get('result',{})
                for key in observed:observed[key].update(payload.get(key,[]))
                if event.get('kind')=='final':final=payload
        def partial(error):
            return {'complete':False,'error':error,
                    **{key:sorted(values) for key,values in observed.items()}}
        try:
            while time.monotonic()<deadline:
                try:
                    resident=int(Path(f'/proc/{pid}/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
                    if resident>max_rss_mb*1024**2:break
                except (OSError,ValueError,IndexError):pass
                ready,_,_=select.select([read_fd],[],[],min(0.025,max(0,deadline-time.monotonic())))
                if ready:
                    data=os.read(read_fd,65536)
                    if not data:finished=True;break
                    consume(data)
            if not finished:
                try:os.killpg(pid,signal.SIGKILL)
                except ProcessLookupError:pass
                return partial('deadline')
            if final is not None:
                for key,values in observed.items():
                    final[key]=sorted(set(final.get(key,[]))|values)
                return final
            return partial('invalid child result')
        finally:
            os.close(read_fd)
            try:os.waitpid(pid,0)
            except ChildProcessError:pass
    def call(self,changes,fn,seconds=10):
        """Run fn() in a forked child that sees the patched sources; None on error or deadline."""
        self.prepare()
        read_fd,write_fd=os.pipe()
        sys.stdout.flush();sys.stderr.flush()
        pid=os.fork()
        if pid==0:
            os.close(read_fd)
            payload={'ok':False}
            try:
                os.setsid()
                with open(os.devnull,'w') as sink:
                    os.dup2(sink.fileno(),1)
                    if not os.environ.get('BR_PROBE_DEBUG'):os.dup2(sink.fileno(),2)
                self._install(changes)
                payload={'ok':True,'value':fn()}
            except BaseException as error:
                payload={'ok':False,'error':type(error).__name__}
            try:
                data=json.dumps(payload).encode()
                while data:
                    written=os.write(write_fd,data);data=data[written:]
            finally:os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        deadline=time.monotonic()+seconds;buffer=b'';finished=False
        try:
            while time.monotonic()<deadline:
                ready,_,_=select.select([read_fd],[],[],min(0.05,max(0,deadline-time.monotonic())))
                if ready:
                    data=os.read(read_fd,65536)
                    if not data:finished=True;break
                    buffer+=data
            if not finished:
                try:os.killpg(pid,signal.SIGKILL)
                except ProcessLookupError:pass
                return None
            try:result=json.loads(buffer)
            except ValueError:return None
            return result.get('value') if result.get('ok') else None
        finally:
            os.close(read_fd)
            try:os.waitpid(pid,0)
            except ChildProcessError:pass
    def _install(self,changes):
        root=self.root
        os.chdir(root);sys.path.insert(0,str(root))
        for name in list(sys.modules):
            if name=='sqlglot' or name.startswith('sqlglot.') or name=='tests' or name.startswith('tests.'):
                del sys.modules[name]
        sys.meta_path.insert(0,PatchFinder(root,changes))
        absolute={str(root/path):value for path,value in changes.items()}
        old_open,old_io_open=builtins.open,io.open
        def opened(original,file,mode='r',*args,**kwargs):
            try:key=os.path.abspath(os.fsdecode(file))
            except (TypeError,ValueError):return original(file,mode,*args,**kwargs)
            if key not in absolute or 'r' not in mode:return original(file,mode,*args,**kwargs)
            data=absolute[key]
            if 'b' in mode:return NamedBytesIO(data,key)
            encoding=kwargs.get('encoding') or (args[1] if len(args)>1 else None) or 'utf-8'
            return NamedStringIO(data.decode(encoding),key,kwargs.get('newline'))
        builtins.open=lambda file,mode='r',*a,**kw:opened(old_open,file,mode,*a,**kw)
        io.open=lambda file,mode='r',*a,**kw:opened(old_io_open,file,mode,*a,**kw)
        return absolute
    def _child(self,changes,test_ids,on_progress=None,case_timeout=4):
        import pytest
        root=self.root
        os.chdir(root);sys.path.insert(0,str(root))
        for name in list(sys.modules):
            if name=='sqlglot' or name.startswith('sqlglot.') or name=='tests' or name.startswith('tests.'):
                del sys.modules[name]
        sys.meta_path.insert(0,PatchFinder(root,changes))
        absolute={str(root/path):value for path,value in changes.items()}
        old_open,old_io_open=builtins.open,io.open
        def opened(original,file,mode='r',*args,**kwargs):
            try:key=os.path.abspath(os.fsdecode(file))
            except (TypeError,ValueError):return original(file,mode,*args,**kwargs)
            if key not in absolute or 'r' not in mode:return original(file,mode,*args,**kwargs)
            data=absolute[key]
            if 'b' in mode:return NamedBytesIO(data,key)
            encoding=kwargs.get('encoding') or (args[1] if len(args)>1 else None) or 'utf-8'
            return NamedStringIO(data.decode(encoding),key,kwargs.get('newline'))
        builtins.open=lambda file,mode='r',*a,**kw:opened(old_open,file,mode,*a,**kw)
        io.open=lambda file,mode='r',*a,**kw:opened(old_io_open,file,mode,*a,**kw)
        # Preserve argument/result serialization while keeping child computations
        # in the same patched interpreter. The baseline suite validates this adapter.
        class PatchedPool(concurrent.futures.ThreadPoolExecutor):
            def shutdown(self, wait=True, *, cancel_futures=True):
                return super().shutdown(wait=wait,cancel_futures=cancel_futures)
            def submit(self,fn,/,*args,**kwargs):
                fn,args,kwargs=pickle.loads(pickle.dumps((fn,args,kwargs)))
                return super().submit(lambda:pickle.loads(pickle.dumps(fn(*args,**kwargs))))
        def pool(*args,**kwargs):
            kwargs.pop('mp_context',None);kwargs.pop('max_tasks_per_child',None)
            kwargs['max_workers']=1
            return PatchedPool(**kwargs)
        concurrent.futures.ProcessPoolExecutor=pool
        # Verify changed modules can initialize before trusting class-level cuts.
        for filename in changes:
            if filename.endswith('.py'):
                module=filename[:-3].replace('/','.')
                if module.endswith('.__init__'):module=module[:-9]
                try:__import__(module)
                except BaseException as error:
                    return {'complete':False,'import_errors':[filename],'error':type(error).__name__}
        if not test_ids:return {'complete':True,'failed':[],'checked':[],'import_errors':[],'exit':0}
        direct=run_tests(test_ids,absolute,on_progress=on_progress,case_timeout=case_timeout)
        if direct is not None:return direct
        outcomes={};import_errors=[];details=[];wanted=set(test_ids)
        class Plugin:
            def pytest_runtest_logreport(self,report):
                name=report.nodeid
                if name not in wanted:return
                if report.failed:
                    outcomes[name]='failed'
                    if len(details)<2:details.append(str(report.longrepr)[-1800:])
                    pytest.exit('A failure was found; keep the conservative selection',returncode=1)
                elif report.when=='call':outcomes.setdefault(name,report.outcome)
            def pytest_collectreport(self,report):
                if report.failed:import_errors.append(report.nodeid)
            @pytest.hookimpl(hookwrapper=True)
            def pytest_runtest_makereport(self,item,call):
                yield
                if call.excinfo:
                    for entry in call.excinfo.traceback:
                        path=str(entry.path)
                        if path in absolute and entry.frame.code.name=='<module>':
                            import_errors.append(path)
                            break
        code=pytest.main([*test_ids,'--maxfail=1','-q','--tb=no','-p','no:cacheprovider','-p','pytest_timeout','--timeout=4'],plugins=[Plugin()])
        return {'complete':all(name in outcomes for name in wanted) and int(code) in [0,1],
                'failed':sorted(name for name,status in outcomes.items() if status=='failed'),
                'checked':sorted(outcomes),'import_errors':import_errors,'exit':int(code),'details':details}
