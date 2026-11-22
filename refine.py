"""Cost-bounded online test execution; no precomputed mutant answers."""
from pathlib import Path
import sys
import time
from probe_runner import Prober

class Refiner:
    def __init__(self,root,index,tests):
        self.root=Path(root)
        self.index=index
        self.by_number={t['number']:t for t in tests}
        self.by_id={t['id']:t['number'] for t in tests}
        self.prober=Prober(root)
        self.started=time.monotonic()
        self.limit=95
        self.stats=[]
    def refine(self,text,selected):
        from selector import parse_patch,apply_change,schedule
        sensitive=getattr(self.index,'import_sensitive',False)
        if self.index.conservative:return selected
        if time.monotonic()-self.started>self.limit-3:
            return self.index.all.copy() if sensitive else selected
        if len(selected)<=1 and not sensitive:return selected
        ceiling=3.0 if len(selected)<=2 else 0.75
        cheap={n for n in selected if self.by_number[n]['seconds']<=ceiling}
        if not cheap and not sensitive:return selected
        durations={n:round(self.by_number[n]['seconds']*1000) for n in selected}
        def cost(numbers):
            workers=schedule(numbers,durations)
            return (max((sum(durations[n] for n in worker) for worker in workers),default=0)
                    +2000*len(workers))/1000
        possible_saving=max(0,cost(selected)-cost(selected-cheap))
        if possible_saving<0.5:
            if not sensitive:return selected
            # Still check initialization when a precise class-field map was used.
            cheap=set()
        # Bound work on very broad changes; unsupported edits keep the static plan.
        if sum(self.by_number[n]['seconds'] for n in cheap)>20:
            return self.index.all.copy() if sensitive else selected
        changes={}
        try:
            for change in parse_patch(text):
                path=change.old
                if path!=change.new or path=='/dev/null' or Path(path).suffix not in {'.py','.sql','.json','.txt'}:
                    return self.index.all.copy() if sensitive else selected
                old=self.index.sources.get(path)
                if old is None:old=(self.root/path).read_text(encoding='utf-8')
                changes[path]=apply_change(old,change).encode('utf-8')
        except (OSError,UnicodeError,ValueError):return self.index.all.copy() if sensitive else selected
        remaining=self.limit-(time.monotonic()-self.started)
        probe_started=time.monotonic()
        trial_budget=min(12,max(0.7,possible_saving*0.7)) if cheap else 3
        result=self.prober.run(changes,[self.by_number[n]['id'] for n in sorted(cheap)],seconds=min(trial_budget,max(1,remaining)))
        print(f'Probe cost: {len(selected)} candidates, {time.monotonic()-probe_started:.3f}s, complete={result.get("complete")}, failed={len(result.get("failed",[]))}',file=sys.stderr)
        if result.get('import_errors') or result.get('setup_errors'):
            return self.index.all.copy()
        checked={self.by_id[name] for name in result.get('checked',[]) if name in self.by_id}&cheap
        if not checked and not result.get('complete'):
            return self.index.all.copy() if sensitive and not result.get('failed') else selected
        failed={self.by_id[name] for name in result['failed'] if name in self.by_id}
        # A test can be removed only after that exact test completed and passed.
        # No passing representative can stand in for a different unexecuted test.
        answer=selected-(checked-failed)
        self.stats.append((len(selected),len(checked),len(answer)))
        print(f'Online validation: {len(selected)} candidates, {len(checked)} completed, {len(answer)} retained',file=sys.stderr)
        return answer
