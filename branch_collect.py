"""Input/effect observations on unchanged code, for the offline collector only."""
import sys
import builtins
from pure_inputs import pure_value,pure_truth,UNKNOWN
from branch_flow import simulate


def descriptor(value):
    truth=pure_truth(value)
    if truth is UNKNOWN:return None
    return (0 if type(value) is bool else 1 if value is None else 2,bool(truth))


class Recorder:
    def __init__(self,registry,context,emit,cap=256):
        self.registry,self.context,self.emit,self.cap=registry,context,emit,cap
        self.frames={};self.counts={};self.bindings={}
    def snapshot(self,frame,token):
        if type(frame.f_locals) is not dict or type(frame.f_globals) is not dict or frame.f_builtins is not vars(builtins):
            return [None]*len(self.registry[token]['leaves'])
        local=dict(frame.f_locals)
        if frame.f_code not in self.bindings:
            self.bindings[frame.f_code]=frame.f_code.co_varnames+frame.f_code.co_cellvars+frame.f_code.co_freevars
        for name in self.bindings[frame.f_code]:local.setdefault(name,UNKNOWN)
        result=[]
        for node in self.registry[token]['leaves']:
            try:
                value=pure_value(node,local,frame.f_globals)
                result.append(None if value is UNKNOWN else descriptor(value))
            except (AttributeError,TypeError,ValueError,KeyError,IndexError):result.append(None)
        return result
    def start(self,token):
        frame=sys._getframe(1);context=self.context();counter=(token,context)
        count=self.counts.get(counter,0)+1;self.counts[counter]=count
        key=(id(frame),token)
        if count>self.cap:
            if count==self.cap+1:self.emit(token,context,None)
            self.frames.setdefault(key,[]).append(None);return None
        record={'context':context,'snapshots':[self.snapshot(frame,token)],'effects':[],
                'references':{},'unsafe':False}
        self.frames.setdefault(key,[]).append(record)
        return None
    def leaf(self,token,index,value):
        frame=sys._getframe(1);stack=self.frames.get((id(frame),token),())
        if not stack or stack[-1] is None:return value
        record=stack[-1];actual=descriptor(value)
        if actual is None:record['unsafe']=True;return value
        prefix=len(record['effects']);snapshot=record['snapshots'][prefix]
        if snapshot[index] is not None:
            if snapshot[index]!=actual:record['unsafe']=True
            record['references'][('p',index,prefix)]=value
        else:
            record['references'][('e',index,prefix)]=value
            record['effects'].append((index,actual))
            record['snapshots'].append(self.snapshot(frame,token))
        return value
    def end(self,token,value):
        frame=sys._getframe(1);key=(id(frame),token);stack=self.frames.get(key)
        if not stack:return value
        record=stack.pop()
        if not stack:del self.frames[key]
        if record is None:return value
        pattern=(self.registry[token]['mode'],record['snapshots'],record['effects'])
        predicted=simulate(self.registry[token]['formula'],pattern)
        actual=descriptor(value)
        valid=not record['unsafe'] and predicted is not None and actual is not None and predicted[:2]==actual
        if valid and predicted[0]==2:
            valid=record['references'].get(predicted[2],UNKNOWN) is value
        self.emit(token,record['context'],pattern if valid else None)
        return value
    def flush(self):
        # Exceptions may prevent an end marker. Such observations cannot prove
        # a patched expression safe, even when the enclosing test catches them.
        for (_,token),stack in self.frames.items():
            for record in stack:
                if record is not None:self.emit(token,record['context'],None)
        self.frames.clear()
