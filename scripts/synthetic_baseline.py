#!/usr/bin/env python3
"""Connected synthetic runner/form reference baseline; aggregate output only.

Uses temporary workspaces and a local Node RPC adapter, never a live driver.
Injected operation/persistence costs are fixture time, not host or live latency.
"""
import argparse
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import select
import tempfile
import time
from unittest.mock import patch

import resume_assets
from runner import Kind, Observation
from runner_workspace import build_runner
import setup_workspace
import spans
import workspace as w

CODE = Path(__file__).resolve().parent
NOW = datetime.fromisoformat('2026-10-20T09:00:00+08:00')
CONTENT = b'Synthetic resume fixture bytes. No personal information.\n'
ASSET = {'account_id':'synthetic', 'asset_id':'opaque', 'revision':'1'}
# RPC responses come from actual Python gates, receipt verification and ingestion.
# Native state belongs to the adapter; observers receive no approved values.
FORM = r'''
const F=require(process.argv[1]), T=require(process.argv[2]);
const clone=x=>JSON.parse(JSON.stringify(x));
const lines=require('node:readline').createInterface({input:process.stdin})[Symbol.asyncIterator]();
let seconds=0;
const send=x=>process.stdout.write(JSON.stringify(x)+'\n');
async function receive(){const line=await lines.next();if(line.done)throw Error('transport_closed');return JSON.parse(line.value);}
async function rpc(kind,data){send({kind,data,seconds});const r=await receive();seconds=r.seconds;if(!r.ok)throw Error('fixture_boundary_refused');return r.value;}
(async()=>{
 const config=await receive(); seconds=config.seconds;
 let telemetry;
 const counts={field_writes:0,uploads:0,path_entries:0,observations:0,skipped_fields:0};
 const scope={kind:'easy_apply',application_id:config.application_id,tab_id:'synthetic-tab',dialog_id:'dialog',dialog_label:'Application',root_token:'root',url:config.url};
 const fields=config.approved.answers.map(r=>({field_id:r.field_id,label:r.question,section:'',type:r.type==='boolean'?'checkbox':r.type,value:r.field_id==='years'?0:clone(r.answer),valid:true,writable:true,required:r.required,options:[],node_token:r.field_id,constraints:{min:'',max:'',maxLength:-1}}));
 fields.push({field_id:'resume',label:'Resume',section:'',type:'file',value:null,platform_asset:config.asset,valid:true,writable:true,required:true,options:[],node_token:'resume',constraints:{min:'',max:'',maxLength:-1}});
 const state={scope,fields};
 const adapter={canUpload:true,
  observe:async()=>{counts.observations++;seconds+=.2;return clone(state);},
  write:async action=>{const field=state.fields.find(f=>f.field_id===action.field_id);
   if(action.kind==='upload_resume'){counts.uploads++;counts.path_entries++;seconds+=.9;const uploaded=await rpc('upload',action.resume);field.value=uploaded.resume;field.platform_asset=uploaded.asset;}
   else{counts.field_writes++;seconds+=.3;field.value=clone(action.value);}
   if(config.fault==='write_ignored' && action.field_id==='years')field.value=0;
  }};
 function configure(context){telemetry=config.instrumented?T.create({context,processId:'synthetic-node',monotonic:()=>seconds,wall:()=>new Date(Date.parse(config.started_at)+seconds*1000),emit:row=>rpc('span',row)}):null;}
 async function currentResume(expected){const current=await rpc('resolve');if(['id','path','sha256'].some(key=>current[key]!==expected[key]))throw Error('resume_changed');}
 async function guarded(phase,fn,channel='tool',counters={},resume=null,persist=true){
  const attempted=Object.fromEntries(Object.keys(counters).map(key=>[key,0]));
  const checked=async()=>{await rpc('guard');if(resume){await currentResume(resume);await rpc('guard');}Object.assign(attempted,counters);return fn();};
  return telemetry && persist?telemetry.call(phase,channel,checked,{counters:attempted}):checked();
 }
 const resolver=()=>rpc('resolve');
 const verifier=request=>rpc('verify',request);
 const remember=request=>rpc('remember',request);
 // Frozen original unconditional schedule, with real checks rather than a
 // declared success. Fresh guards/resolver, exact layout and readback, reactive
 // reset checks, platform-byte receipts, and final independent review apply.
 async function reference(plan,observation){
  const layout=view=>JSON.stringify({scope:view.scope,fields:view.fields.map(({value,valid,platform_asset,...descriptor})=>descriptor)});
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  async function observe(){const view=await guarded('form_observation',adapter.observe,'browser',{observations:1});if(layout(view)!==layout(observation))throw Error('page_changed');return view;}
  async function resolve(){if(!same(await guarded('resume_resolution',resolver),config.resume))throw Error('resume_changed');}
  async function check(view,actions,persist=true){for(const action of actions){const field=view.fields.find(f=>f.field_id===action.field_id);if(field.valid===false)throw Error('invalid_field');
   if(action.kind==='upload_resume'){if(!await guarded('resume_reuse',()=>verifier({asset:field.platform_asset,resume:action.resume}),'tool',{},action.resume,persist))throw Error('unverified_resume');}
   else if(!same(field.value,action.value))throw Error('readback_mismatch');}}
  await observe();await resolve();const written=[];
  for(const action of plan.actions){const before=await observe();await check(before,written);if(action.kind==='upload_resume')await resolve();
   await guarded(action.kind==='upload_resume'?'resume_upload':'form_filling',()=>adapter.write(action),'browser',action.kind==='upload_resume'?{uploads:1}:{field_writes:1},config.resume);
   const after=await observe();if(action.kind==='upload_resume')await guarded('resume_reuse',()=>remember({asset:after.fields.find(f=>f.field_id===action.field_id).platform_asset,resume:action.resume}),'tool',{},action.resume);
   written.push(action);await check(after,written);
  }
  const observed=await observe();await check(observed,plan.actions,false);
  await currentResume(config.resume);await rpc('guard');
  return {status:'verified',observed};
 }
 configure(config.context);
 const observation=clone(state);
 const plan=F.plan({observation,approved:config.approved,bindings:fields.map(f=>f.type==='file'?{field_id:f.field_id}:{field_id:f.field_id,occurrence_id:config.approved.answers.find(r=>r.field_id===f.field_id).occurrence_id}),resume:config.resume});
 if(plan.status!=='planned')throw Error('plan_failed');
 const result=config.reference?await reference(plan,observation):await F.execute(plan,adapter,{guard:()=>rpc('guard'),resumeResolver:resolver,resumeVerifier:verifier,rememberResume:remember,telemetry});
 if(result.status!=='verified')throw Error('form_not_verified');
 if(result.skipped)counts.skipped_fields=result.skipped.filter(id=>id!=='resume').length;
 send({kind:'result',value:{observed:result.observed,counts},seconds});
 const command=await receive();seconds=command.seconds;configure(command.context);
 if(command.command!=='review')throw Error('invalid_command');
 const observed=await guarded('form_observation',adapter.observe,'browser',{observations:1});
 send({kind:'result',value:{observed,counts},seconds});
})().catch(()=>{send({kind:'error',seconds});process.exitCode=1;});
'''


class Clock:
    def __init__(self): self.seconds = 0.
    def mono(self): return self.seconds
    def wall(self): return NOW + timedelta(seconds=self.seconds)
    def advance(self, seconds): self.seconds += seconds


def initialize(root):
    setup_workspace.init_workspace(root)
    w.atomic_json(root/'settings.json', {'resume_scope':'backend'})
    releases = []
    for identity, purpose in [('master','reference'), ('backend','application')]:
        path = root/f"resumes/{identity}{'.pdf' if purpose == 'application' else '.md'}"; path.write_bytes(CONTENT)
        releases.append({'id':identity, 'scope':identity, 'purpose':purpose, 'status':'active',
            'path':str(path.relative_to(root)), 'sha256':w.digest(path),
            'released_at':(NOW-timedelta(days=1)).isoformat(), 'effective_from':(NOW-timedelta(days=1)).isoformat(),
            'review_due_at':(NOW+timedelta(days=30)).isoformat(), 'valid_until':None,
            'source_master_id':'master' if purpose=='application' else None, 'selected_projects':[]})
    w.atomic_json(root/'resumes/registry.json', {'schema_version':1, 'releases':releases})
    questions = [{'question':'Synthetic '+identity, 'field_id':identity, 'type':kind, 'required':True}
                 for identity,kind in [('name','text'), ('years','number'), ('ordinary','boolean')]]
    w.atomic_json(root/'profile/answers.json', {'schema_version':1, 'answers':[
        {'id':q['field_id'], 'value':value, 'type':q['type'], 'scope':{}, 'aliases':[q['question']],
         'source':'Synthetic fixture confirmation', 'confirmed_at':(NOW-timedelta(days=1)).isoformat()}
        for q,value in zip(questions,['Example',1,True])]})
    w.settings.run_start(root, 'synthetic-run', NOW)
    brief=root/'brief.json';w.atomic_json(brief, {'batch_id':'synthetic-batch','target':1})
    app=w.capture(root, {'source':'linkedin_job_page','company':'Example','role':'Engineer','location':'Singapore',
        'platform':'linkedin','platform_job_id':'123456','listing_url':'https://www.linkedin.com/jobs/view/123456',
        'job_description':'Synthetic full-time engineer role. No minimum experience.',
        'run_id':'synthetic-run','batch_id':'synthetic-batch'}, NOW, deferred=True)['application_id']
    # Independently held platform bytes, not a read of the expected source file.
    asset_file=root/'synthetic-platform.bin';asset_file.write_bytes(CONTENT)
    resume_assets.remember(root,'backend',ASSET,asset_file.read_bytes(),NOW,method='verified_download')
    w.recover(root,w.export_csv)
    return app, brief, questions, asset_file


def selection(current):
    return {key:current[key] for key in ('id','path','sha256')}


class FormBridge:
    """Local RPC transport under the runner's existing writer; no nested CLI."""
    def __init__(self, root, clock, boundary, controller, action):
        self.root,self.clock,self.boundary,self.controller,self.action=root,clock,boundary,controller,action
        self.process=subprocess.Popen(['node','-e',FORM,str(CODE/'form_handlers.js'),str(CODE/'telemetry.js')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)

    def close(self):
        if self.process.poll() is None: self.process.terminate()
        self.process.wait(timeout=5)
        self.process.stdin.close();self.process.stdout.close()

    def exchange(self, message):
        self.process.stdin.write(json.dumps({**message,'seconds':self.clock.seconds})+'\n');self.process.stdin.flush()
        while True:
            if not select.select([self.process.stdout], [], [], 10)[0]:
                raise ValueError('synthetic_transport_timeout')
            line=self.process.stdout.readline()
            if not line: raise ValueError('synthetic_transport_closed')
            request=json.loads(line);self.clock.seconds=request['seconds']
            kind,data=request['kind'],request.get('data')
            if kind=='error': raise ValueError('synthetic_form_failed')
            if kind=='result': return request['value']
            try:
                if kind=='span': value=self.controller.telemetry.ingest(data)
                elif kind=='guard':
                    spans.authorize(lambda:self.controller.hooks.native_guard(self.action,self.clock.wall()));value=True
                elif kind=='resolve': value=selection(self.boundary.resolve())
                elif kind=='verify': value=self.boundary.verify(data)
                elif kind=='remember': value=self.boundary.remember(data)
                elif kind=='upload':
                    current=selection(self.boundary.resolve())
                    if data!=current: raise ValueError('resume_changed')
                    content=(self.root/current['path']).read_bytes()
                    self.root.joinpath('synthetic-platform.bin').write_bytes(content)
                    value={'resume':{**current,'sha256':w.digest(self.root/'synthetic-platform.bin')},'asset':ASSET}
                else: raise ValueError('synthetic_transport_invalid')
                response={'ok':True,'value':value}
            except (ValueError,OSError): response={'ok':False}
            self.process.stdin.write(json.dumps({**response,'seconds':self.clock.seconds})+'\n');self.process.stdin.flush()


def connected_run(reference=False, instrumented=True, *, fault=None):
    """Same fixture/gates in both schedules. Returns aggregates, never artifacts."""
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        root=Path(directory).resolve();app,brief,questions,asset_file=initialize(root)
        clock=Clock();counts={'record_reads':0,'exports':0,'telemetry_persistence_calls':0,'clicks':0}
        read,export,save=w.read,w.export_csv,spans.Recorder._save
        def counted_read(path):
            if path.name=='record.json': counts['record_reads']+=1;clock.advance(.01)
            return read(path)
        def counted_export(*args,**kw):
            counts['exports']+=1;clock.advance(.1)
            return export(*args,**kw)
        def counted_save(recorder):
            counts['telemetry_persistence_calls']+=1;clock.advance(.001)
            return save(recorder)
        class Adapter:
            bridge=None
            observed=None
            form_counts=None
            def observe_url(self,action):
                clock.advance(.2)
                return w.read(root/action.record_ref)['listing_url']
            def perform(self,action,recorder):
                context={'run_id':'synthetic-run','application_id':app,'action_id':str(action.sequence),
                         'parent_id':recorder.stack[-1]['span_id'] if recorder.stack else None}
                if action.kind==Kind.PREPARE:
                    answer=w.prepare_answers(root,app,questions,clock.wall(),deferred=not reference)
                    if not answer['ready']: raise ValueError('synthetic_answers_unresolved')
                    w.event(root,argparse.Namespace(id=app,status='ready',note='',resume_scope='backend',answers=root/answer['answers_path'],evidence=None),clock.wall(),deferred=not reference)
                    prepared=w.read(root/action.record_ref)['prepared'];context['attempt_id']=prepared['attempt_id']
                    recorder.correlate(action,prepared['attempt_id'])
                    def guard(): controller.hooks.native_guard(action,clock.wall());return True
                    def read_asset(asset):
                        if asset!=ASSET: raise ValueError('synthetic_asset_changed')
                        return asset_file.read_bytes()
                    boundary=resume_assets.Boundary(root,'backend',guard=guard,clock=clock.wall,read_asset_bytes=read_asset)
                    if fault=='receipt_corrupt':
                        state=w.read(root/'resumes/platform-assets.json');state['receipts'][0]['sha256']='0'*64
                        w.atomic_json(root/'resumes/platform-assets.json',state)
                    self.bridge=FormBridge(root,clock,boundary,controller,action);stack.callback(self.bridge.close)
                    result=self.bridge.exchange({'reference':reference,'instrumented':instrumented,'fault':fault,
                        'application_id':app,'url':w.read(root/action.record_ref)['listing_url'],'resume':selection(boundary.resolve()),
                        'asset':ASSET,'approved':{'snapshot_id':answer['snapshot_version'],'application_id':app,'answers':answer['answers']},
                        'context':context,'started_at':NOW.isoformat()})
                    self.observed,self.form_counts=result['observed'],result['counts']
                elif action.kind==Kind.REVIEW:
                    prepared=w.read(root/action.record_ref)['prepared'];context['attempt_id']=prepared['attempt_id']
                    self.bridge.action=action
                    result=self.bridge.exchange({'command':'review','context':context})
                    self.observed,self.form_counts=result['observed'],result['counts']
                    observed=self.observed
                    if fault=='review_mismatch': asset_file.write_bytes(b'Changed synthetic platform bytes')
                    # Independent platform bytes and a fresh native observation,
                    # not the executor's claim of verification, establish review.
                    visible={'attempt_id':prepared['attempt_id'],'resume_sha256':w.digest(asset_file),
                        'fields':[{'occurrence_id':r['occurrence_id'],'question':r['question'],'type':r['type'],
                            'value':next(f['value'] for f in observed['fields'] if f['field_id']==r['field_id'])}
                            for r in w.read(root/prepared['answers_path'])]}
                    review=w.review_form(root,app,visible,clock.wall(),deferred=not reference)
                    if not review['passed']: raise ValueError('synthetic_review_failed')
                elif action.kind==Kind.SUBMIT:
                    record=w.read(root/action.record_ref)
                    if not record.get('submission_intent'): raise ValueError('synthetic_intent_required')
                    def confirm():
                        counts['clicks']+=1;clock.advance(.2)
                        evidence=root/'confirmation.txt';evidence.write_text('Observed synthetic confirmation. No live submission.\n')
                        w.event(root,argparse.Namespace(id=app,status='submitted',note='',resume_scope=None,answers=None,evidence=evidence),clock.wall(),deferred=not reference)
                    recorder.call('confirmation','browser',confirm)
                return Observation()
        adapter=Adapter();controller=build_runner(root,'synthetic-run',brief,adapter,monotonic=clock.mono)
        if not instrumented: controller.telemetry.available=False
        stack.enter_context(patch.object(w,'read',side_effect=counted_read))
        stack.enter_context(patch.object(w,'export_csv',side_effect=counted_export))
        stack.enter_context(patch.object(spans.Recorder,'_save',counted_save))
        if reference: stack.enter_context(patch.object(w,'planning_snapshot',side_effect=lambda root:nullcontext()))
        result=controller.execute(clock=clock.wall,max_steps=5)
        elapsed=clock.seconds
        # Read-only validation occurs outside the measured workflow/counters.
        record=read(root/f'applications/records/{app}/record.json')
        rows=spans.load(root)
        report=spans.summarize([record],rows)
        complete=[a.kind for a in result]==[Kind.PREPARE,Kind.REVIEW,Kind.SUBMIT,Kind.COMPLETE]
        if not complete or record['status']!='submitted' or counts['clicks']!=1:
            raise ValueError('synthetic_workflow_not_verified')
        if instrumented and (not controller.telemetry.available or any(r['coverage']!='measured' for r in rows)):
            raise ValueError('synthetic_spans_incomplete')
        return {**counts,**adapter.form_counts,'elapsed_seconds':round(elapsed,6),
            'wall_run_seconds':round(report['wall_run_seconds'],6) if report['wall_run_seconds'] is not None else None,
            'measured_active_seconds':round(report['critical_path_active_seconds'],6) if report['critical_path_active_seconds'] is not None else None,
            'unique_confirmations':report['unique_confirmed_applications'],
            'confirmations_per_wall_hour':round(report['confirmations_per_wall_hour'],6) if report['confirmations_per_wall_hour'] is not None else None,
            'confirmations_per_measured_active_hour':round(report['confirmations_per_measured_active_hour'],6) if report['confirmations_per_measured_active_hour'] is not None else None,
            'time_to_first_confirmation_seconds':round(report['time_to_first_confirmation_seconds'],6) if report['time_to_first_confirmation_seconds'] is not None else None,
            'measured_spans':report['measured_spans'],
            'imported_form_spans':sum(r['process_id']=='synthetic-node' for r in rows),
            'workflow_completed':complete, 'export_clean':not w.export_state(root)['dirty']}


def baseline():
    before,after=connected_run(reference=True),connected_run()
    bare_before,bare_after=connected_run(reference=True,instrumented=False),connected_run(instrumented=False)
    return {'schema_version':2,'fixture':'connected-synthetic-four-field-one-mismatch-v2',
        'model_configuration':'none; identical fixture and no model calls in every variant',
        'clock':'fake; observation=0.2s, field_write=0.3s, upload_and_path=0.9s, record_read=0.01s, export=0.1s, durable_span_save=0.001s',
        'reference':'frozen unconditional form schedule plus uncached planning and immediate mutation exports; same current safety, readback, receipt, independent review and submit gates',
        'confirmation_evidence':'local synthetic confirmation via real submit-intent, event and verification flow; no live submission',
        'latency_scope':'connected Runner.execute through bounded checkpoint; all record reads, exports, guarded form RPC and JS span ingestion included',
        'latency_samples_per_variant':1,'before':before,'after':after,
        'before_workflow_latency':spans.quantiles([before['elapsed_seconds']]),
        'after_workflow_latency':spans.quantiles([after['elapsed_seconds']]),
        'paired_without_telemetry':{'before_elapsed_seconds':bare_before['elapsed_seconds'],'after_elapsed_seconds':bare_after['elapsed_seconds']},
        'instrumentation_overhead_seconds':{'before':round(before['elapsed_seconds']-bare_before['elapsed_seconds'],6),'after':round(after['elapsed_seconds']-bare_after['elapsed_seconds'],6)},
        'live_speedup':None}


def host_overhead(samples=20):
    """Optional measured local persistence overhead; no stable timing assertion."""
    bare,instrumented=[],[]
    with tempfile.TemporaryDirectory() as directory:
        recorder=spans.Recorder(Path(directory).resolve(),'synthetic-overhead')
        fixture=tuple(range(100))
        for _ in range(samples):
            before=time.perf_counter();sum(fixture);bare.append(time.perf_counter()-before)
            before=time.perf_counter();recorder.call('context_loading','tool',lambda:sum(fixture));instrumented.append(time.perf_counter()-before)
    return {'clock':'host perf_counter; temporary local durable ledger','samples':samples,
        'bare':spans.quantiles(bare),'instrumented':spans.quantiles(instrumented),
        'paired_overhead':spans.quantiles([max(0.,b-a) for a,b in zip(bare,instrumented)])}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--measure-overhead',action='store_true')
    args=parser.parse_args();result=baseline()
    if args.measure_overhead: result['host_instrumentation_overhead']=host_overhead()
    print(json.dumps(result,indent=2,sort_keys=True))
