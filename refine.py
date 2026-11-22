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
        from selector import parse_patch,apply_change
        sensitive=getattr(self.index,'import_sensitive',False)
        if self.index.conservative:return selected
        if time.monotonic()-self.started>self.limit-3:
            return self.index.all.copy() if sensitive else selected
        if len(selected)<=1 and not sensitive:return selected
        cheap={n for n in selected if self.by_number[n]['seconds']<=0.75}
        if not cheap and not sensitive:return selected
        # Bound work on very broad changes; unsupported edits keep the static plan.
        if sum(self.by_number[n]['seconds'] for n in cheap)>10:
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
        result=self.prober.run(changes,[self.by_number[n]['id'] for n in sorted(cheap)],seconds=min(14,max(1,remaining)))
        print(f'Probe cost: {len(selected)} candidates, {time.monotonic()-probe_started:.3f}s, complete={result.get("complete")}, failed={len(result.get("failed",[]))}',file=sys.stderr)
        if result.get('import_errors') or result.get('setup_errors'):
            return self.index.all.copy()
        if not result.get('complete'):
            return self.index.all.copy() if sensitive and not result.get('failed') else selected
        failed={self.by_id[name] for name in result['failed'] if name in self.by_id}
        untested=selected-cheap
        if failed:
            answer=failed|untested
        else:
            # An expensive test is retained if its changed-line/class dependency
            # has no passing inexpensive representative. This is a prediction,
            # not a proof that unseen input-sensitive failures are impossible.
            answer=set()
            for number in untested:
                relevant=[group for group in self.index.witnesses if number in group]
                if not relevant or any(not (group&cheap) for group in relevant):answer.add(number)
        self.stats.append((len(selected),len(cheap),len(answer)))
        print(f'Online validation: {len(selected)} candidates, {len(cheap)} executed, {len(answer)} retained',file=sys.stderr)
        return answer
