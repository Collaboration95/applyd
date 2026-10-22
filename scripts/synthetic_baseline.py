#!/usr/bin/env python3
"""Reproduce a synthetic operation-count baseline; outputs aggregates only.

Fixed fake operation costs illustrate accounting, not measured live speed.
Neither variant invokes a model, browser, network or personal workspace.
"""
import argparse
import json
import time
import spans
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
from datetime import datetime
import setup_workspace
import workspace as w

CODE = Path(__file__).resolve().parent
FORM = r'''
const F = require(process.argv[1]);
const T = require(process.argv[2]);
const clone = x => JSON.parse(JSON.stringify(x));
const resume = {id:'synthetic-release',path:'/synthetic/fixture.pdf',sha256:'a'.repeat(64)};
const scope = {kind:'easy_apply', application_id:'synthetic-app',tab_id:'tab',dialog_id:'dialog',dialog_label:'Application',root_token:'root',url:'https://www.linkedin.com/jobs/view/123'};
const fields = [['name','text','Example', 'Example'],['years','number',0,1],['consent','checkbox',true,true],['resume','file',null,null]].map(([id,type,value])=>({field_id:id,label:id==='resume'?'Resume':id,section:'',type,value,valid:true,writable:true,required:true,options:[],node_token:id,constraints:{min:'',max:'',maxLength:-1}}));
const approved = {snapshot_id:'synthetic-snapshot',application_id:scope.application_id,answers:fields.filter(f=>f.type!=='file').map(f=>({occurrence_id:f.field_id,field_id:f.field_id,application_id:scope.application_id,question:f.label,type:f.type==='checkbox'?'boolean':f.type,options:[],required:true,resolution:'resolved',answer:f.field_id==='years'?1:f.value}))};
const bindings = fields.map(f=>f.type==='file'?{field_id:f.field_id}:{field_id:f.field_id,occurrence_id:f.field_id});
async function run(legacy,instrumented) {
 let seconds=0, emissions=0;const counts={field_writes:0,uploads:0,path_entries:0,observations:0,skipped_fields:0};
 const state={scope,fields:clone(fields)};
 state.fields.at(-1).platform_asset={account_id:'synthetic',asset_id:'opaque',revision:'1'};
 const clock=()=>seconds;
 const telemetry=instrumented?T.create({context:{run_id:'synthetic-run'},processId:'fixture-process',monotonic:clock,wall:()=>new Date(Date.parse('2026-10-20T01:00:00Z')+seconds*1000),emit:()=>{seconds+=.001;emissions++;}}):null;
 const adapter={canUpload:true,observe:async()=>{counts.observations++;seconds+=.2;return clone(state);},write:async action=>{const f=state.fields.find(f=>f.field_id===action.field_id);if(action.kind==='upload_resume'){counts.uploads++;counts.path_entries++;seconds+=.9;f.value=clone(resume);delete f.platform_asset;}else{counts.field_writes++;seconds+=.3;f.value=action.value;}}};
 const observation={scope,fields:clone(state.fields)};
 const plan=F.plan({observation,approved,bindings,resume});
 let status;
 if(legacy){
   // Frozen original execute schedule: initial observation, before+after every
   // unconditional action, final observation. Same actual fake adapter/input.
   await adapter.observe();
   for(const action of plan.actions){await adapter.observe();await adapter.write(action);await adapter.observe();}
   await adapter.observe();status='verified';
 }else{
   const result=await F.execute(plan,adapter,{guard:()=>true,resumeResolver:()=>clone(resume),resumeVerifier:({asset})=>asset.asset_id==='opaque',telemetry});
   status=result.status;counts.skipped_fields=result.skipped.filter(id=>id!=='resume').length;
 }
 if(status!=='verified')throw Error('fixture_failed');
 return {...counts,seconds:Number(seconds.toFixed(6)),telemetry_emissions:emissions};
}
(async()=>console.log(JSON.stringify({before:await run(true,false),after:await run(false,false),instrumented:await run(false,true)})))();
'''


def planning_and_exports(deferred):
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory).resolve(); setup_workspace.init_workspace(root)
        counts={'record_snapshot_reads':0,'exports':0}
        original_read, original_export=w.read,w.export_csv
        def read(path):
            if path.name=='record.json': counts['record_snapshot_reads']+=1
            return original_read(path)
        def export(*args,**kw):
            counts['exports']+=1
            return original_export(*args,**kw)
        record={'application_id':'synthetic','company':'Example','role':'Engineer','location':'Example City','platform':'linkedin','listing_url':'https://www.linkedin.com/jobs/view/123','status':'discovered','events':[],'submissions':[]}
        w.atomic_json(root/'applications/records/synthetic/record.json',record)
        with w.writer(root),patch.object(w,'read',side_effect=read),patch.object(w,'export_csv',side_effect=export):
            if deferred:
                with w.planning_snapshot(root):
                    for _ in range(3):w.records(root)
            else:
                for _ in range(3):w.records(root)
            for _ in range(3):w.finish_mutation(root,{'synthetic_mutation':True},deferred=deferred)
            if deferred:w.recover(root,w.export_csv)
        return counts


def baseline():
    forms=json.loads(subprocess.run(['node','-e',FORM,str(CODE/'form_handlers.js'),str(CODE/'telemetry.js')],capture_output=True,text=True,check=True,timeout=10).stdout)
    before,after=planning_and_exports(False),planning_and_exports(True)
    result={'schema_version':1,'fixture':'synthetic-four-field-one-mismatch-v1','model_configuration':'none; no model calls in either variant',
        'clock':'fake; observation=0.2s, field_write=0.3s, upload_and_path=0.9s, telemetry_emit=0.001s',
        'confirmation_evidence':'none; no submit operation exists in this fixture','before':{**forms['before'],**before},'after':{**forms['after'],**after},
        'instrumentation_overhead_seconds':round(forms['instrumented']['seconds']-forms['after']['seconds'],6),
        'instrumentation_emissions':forms['instrumented']['telemetry_emissions'],
        'latency_scope':'form-step only; record/export counts are separate untimed actual calls',
        'latency_samples_per_variant':1,
        'before_form_latency':spans.quantiles([forms['before']['seconds']]),
        'after_form_latency':spans.quantiles([forms['after']['seconds']]),
        'throughput_denominators':{'before_wall_seconds':forms['before']['seconds'],'before_measured_active_seconds':forms['before']['seconds'],'after_wall_seconds':forms['after']['seconds'],'after_measured_active_seconds':forms['after']['seconds']},
        'unique_confirmations':0,'confirmations_per_wall_hour':0,'confirmations_per_measured_active_hour':0,
        'time_to_first_confirmation_seconds':None,'live_speedup':None}
    return result


def host_overhead(samples=20):
    """Optional measured local persistence overhead; no stable timing assertion."""
    bare, instrumented = [], []
    with tempfile.TemporaryDirectory() as directory:
        recorder = spans.Recorder(Path(directory).resolve(), 'synthetic-overhead')
        fixture = tuple(range(100))
        for _ in range(samples):
            before = time.perf_counter(); sum(fixture); bare.append(time.perf_counter()-before)
            before = time.perf_counter()
            recorder.call('context_loading','tool',lambda:sum(fixture))
            instrumented.append(time.perf_counter()-before)
    return {'clock':'host perf_counter; temporary local durable ledger', 'samples':samples,
            'bare':spans.quantiles(bare),'instrumented':spans.quantiles(instrumented),
            'paired_overhead':spans.quantiles([max(0.,b-a) for a,b in zip(bare,instrumented)])}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--measure-overhead',action='store_true')
    args=parser.parse_args()
    result=baseline()
    if args.measure_overhead: result['host_instrumentation_overhead']=host_overhead()
    print(json.dumps(result,indent=2,sort_keys=True))
