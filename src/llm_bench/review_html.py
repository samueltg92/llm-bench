"""Offline private comparison of all models, metrics and exact histories."""

import json

from .privacy import write_private
from .review_metrics import METRIC_HELP
from .review_share import shareable_data
from .review_static import render_static


def render_review(records, path, *, project_rows=None, common_rows=None,
                  project_names=None, consolidated=None, followup_count=0, model_profiles=None,
                  historical_count=0, generated_at="", baseline=None, profile_notes=None, synthetic=False,
                  shareable=False, publication_check=None):
    # Model output is untrusted. Escape the data block and use textContent only.
    data = {"records": records, "project_rows": project_rows or [],
            "common_rows": common_rows or [], "project_names": project_names or {},
            "generated_at": generated_at, "consolidated": consolidated or [],
            "followup_count": followup_count, "model_profiles": model_profiles or {},
            "historical_count": historical_count, "baseline": baseline or {},
            "profile_notes": profile_notes or {}, "synthetic": synthetic}
    if shareable:
        if publication_check is None:
            raise ValueError("Shareable HTML requires a publication check")
        data = shareable_data(data)
        publication_check(json.dumps(data, ensure_ascii=False))
    data["metric_help"] = METRIC_HELP
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    document = TEMPLATE.replace("__RECORDS__", payload)
    if shareable:
        document = document.replace('<main>', render_static(data) + '<main id="interactive-report" hidden>', 1)
        document = document.replace(
            "Selected LLMs: metrics, scenarios and conversations in one comparison.",
            "Selected LLMs: results by project and scenario, consolidated metrics and reasoning profiles.",
        ).replace(
            "Private file with project names and original content. Works offline; tool responses are simulated. Source conversations retain their original language.",
            "Shareable metrics report. Anonymous projects and scenarios; no private conversations or prompts. Works offline; tool responses were simulated.",
        )
        publication_check(document)
    write_private(path, document, plain=True)


TEMPLATE = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Benchmark by project</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f2f5f8;color:#142b42;font:14px system-ui,sans-serif}
header{padding:24px 3vw;background:#142b42;color:white}h1{font-size:26px;margin:0 0 8px}
header p{margin:5px 0;color:#d9e4ec}.stamp{font-size:12px;color:#9adde0}main{padding:20px 3vw}
label{display:inline-grid;gap:6px;margin:0 20px 12px 0;font-weight:600}
select,button{font:inherit;padding:9px;border:1px solid #cad5df;border-radius:6px;background:white;max-width:100%;color:#142b42}
[hidden]{display:none!important}button:disabled{opacity:.5;cursor:default}button{cursor:pointer}button[aria-pressed=true]{background:#087f8c;color:white;border-color:#087f8c}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.toolbar h2{margin-right:auto}
h2{font-size:19px;margin:0 0 10px}h3{font-size:16px;margin:0 0 10px}.box{background:white;border-radius:10px;padding:18px;margin-bottom:20px}
.scroll{overflow:auto}.metrics{border-collapse:collapse;width:100%;min-width:850px;table-layout:fixed}
.metrics th,.metrics td{padding:9px 12px;text-align:right;border-bottom:1px solid #e3eaf0;vertical-align:top}
.metrics th:first-child,.metrics td:first-child{text-align:left;width:24%}.metrics thead{background:#142b42;color:white}
.metrics tbody tr:nth-child(even){background:#f2f6f9}.metrics td{font-variant-numeric:tabular-nums}
.note,.meta{color:#536575;font-size:12px;line-height:1.5}.note{margin:10px 0 0}.description{font-size:14px;line-height:1.5;margin:4px 0 14px}
.panes{display:grid;grid-template-columns:repeat(var(--model-count,4),minmax(260px,1fr));gap:14px;min-width:calc(var(--model-count,4) * 274px)}
.pane{min-width:0;background:white;border-radius:10px;padding:15px}.pane h3{color:#087f8c;position:sticky;top:0;background:white;padding:4px 0;z-index:1}
.meta{padding-bottom:12px;border-bottom:1px solid #dde5ed}.mini{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:12px 0}
.mini div{padding:8px 5px;background:#f2f6f9;border-radius:5px;font-size:11px}.mini b{display:block;font-size:13px;margin-bottom:3px}
.message{margin:12px 0;padding:10px;border-radius:7px;background:#f2f5f8;overflow-wrap:anywhere}
.assistant{background:#eaf7f5}.role{font-size:10px;font-weight:700;color:#087f8c;margin-bottom:6px}
.content{white-space:pre-wrap;line-height:1.5}details{margin-top:8px}summary{cursor:pointer}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:11px ui-monospace,monospace}.empty{color:#657889;font-style:italic}
.metric-help{display:inline;padding:2px 0;border:0;border-bottom:1px dotted #087f8c;border-radius:0;background:transparent;color:inherit;text-align:left;cursor:help;font:inherit;line-height:1.5}
.metric-help:focus-visible{outline:2px solid #087f8c;outline-offset:3px}.metric-help::after{content:' ⓘ';color:#087f8c;font-size:12px}
.metric-tooltip{position:fixed;z-index:20;max-width:min(390px,calc(100vw - 24px));padding:14px 16px;background:#142b42;color:white;border-radius:8px;box-shadow:0 6px 24px #142b4240;font-size:13px;line-height:1.55;max-height:60vh;overflow:auto}
.metric-tooltip b{display:block;margin-bottom:7px}.metric-tooltip p{margin:0}
.static-project>summary{font-size:20px;margin-bottom:18px}.static-project h3{margin-top:22px}
.static-table{margin:12px 0 22px}.static-help{margin:0}.static-help summary{color:#087f8c}
.static-scenario{margin:12px 0;border-top:1px solid #cad5df;padding-top:12px}
#static-report p{line-height:1.55}.static-table td:first-child{background:#f2f6f9}
@media(max-width:600px){.static-table{min-width:680px}.static-table td:first-child,.static-table th:first-child{position:sticky;left:0;z-index:1}.static-table th:first-child{background:#142b42}.box{padding:12px}}
@media print{header,main{padding:12px}.scroll{overflow:visible}.panes{min-width:0;grid-template-columns:repeat(2,1fr)}.metrics{min-width:0;font-size:10px}}
</style>
<header><h1>Benchmark by project</h1><p>Selected LLMs: metrics, scenarios and conversations in one comparison.</p>
<p class="stamp" id="stamp"></p><p class="stamp" id="privacy-note">Private file with project names and original content. Works offline; tool responses are simulated. Source conversations retain their original language.</p></header>
<main><label>Project<select id="project"></select></label><label>Simulated scenario<select id="case"></select></label>
<p class="description">Hover over, focus or tap a metric name for its definition and measurement method. All data and explanations are embedded in this file; no internet connection is needed.</p>
<button id="show-reading-view" hidden>Complete reading view</button>
<section class="box"><div class="toolbar"><h2 id="metric-title">Project metrics</h2>
<button id="scope-project" aria-pressed="true">Entire project</button><button id="scope-common" aria-pressed="false">Same cases across all models</button><button id="scope-case" aria-pressed="false">Selected scenario</button></div>
<div class="scroll"><table class="metrics" id="metrics"></table></div><p class="note" id="metric-note"></p></section>
<section class="box" id="profile-comparison" hidden><h2>Initial vs current measurements</h2>
<label>Model<select id="profile-model"></select></label><p class="description" id="profile-scope"></p>
<p class="description" id="profile-context"></p><div class="scroll"><table class="metrics" id="profile-metrics"></table></div>
<p class="note">Each profile shows mean, min–max and N. Change = current mean minus initial mean, in the metric's units (percentage points for %). Available cases in the selected project are used, even when a single scenario is selected above. Timing includes complete conversations only; quality includes evaluable failures. Sample sizes and completion may differ. These are separate recorded runs, with uncontrolled cache and timing conditions; differences do not isolate the effect of reasoning. Targeted retries also affect the current selection.</p></section>
<section class="box"><details><summary><b>Metric definitions, cost scope and reasoning settings</b></summary>
<p class="description"><b>TTFT:</b> time from an API request to the first text or tool delta; internal reasoning is not user-facing text. <b>Full latency:</b> time to the end of that API response. <b>First user-facing text:</b> time from a simulated user turn to its first spoken response, including any preceding LLM/tool steps. <b>Flow latency:</b> the full simulated turn across its LLM calls and mock tool results. Local quota waits are excluded and reported separately in the summary.</p>
<p class="description"><b>Expected tools:</b> passed positive function-call expectations divided by all positive expectations, including those on unreached turns. A native call must have the expected name, valid arguments matching the specified subset and the required count in the specified turn. Merely announcing a call does not pass. Backend execution is mocked.</p>
<p class="description"><b>Explicit case rules:</b> passed programmed assertions divided by all applicable assertions: required/prohibited text patterns, word limits, expected active node, forbidden tools, per-turn tool limits and specified tool-before-text order. This is not an exhaustive semantic assessment of every prompt instruction. <b>Ordered flow milestones:</b> fraction of applicable conversations whose actual node path contains the expected milestones in order; extra steps are allowed and repeated milestones remain significant.</p>
<p class="description"><b>Total known cost:</b> sum of known call costs for the selected scope and model. This is not a per-call price. The consolidated view separates cost per API call and per conversation. Earlier attempts and diagnostics outside the selected scope are not included in this total. Unknown usage is not assumed free; calculated costs are not invoices.</p>
<p class="description"><b>Reasoning profiles:</b> <span id="profile-description"></span> Effort support and the meaning of each setting depend on the model and provider. Different profiles and cache behavior can affect latency and quality. Profiles are read from recorded experiment settings.</p>
<p class="note" id="followup-note"></p></details></section>
<section id="conversation-section"><div class="toolbar"><h2>Scenario conversations</h2><label id="turn-control">Show<select id="turn"></select></label></div>
<p id="description" class="description"></p><div class="scroll"><div class="panes" id="panes"></div></div></section></main>
<script type="application/json" id="records">__RECORDS__</script>
<script>
const data=JSON.parse(document.getElementById('records').textContent),records=data.records;
const byId=id=>document.getElementById(id),unique=xs=>[...new Set(xs)];
const tooltip=node('aside','','metric-tooltip');tooltip.id='metric-tooltip';tooltip.setAttribute('role','tooltip');tooltip.hidden=true;document.body.append(tooltip);let activeHelp=null;
function closeHelp(){tooltip.hidden=true;if(activeHelp)activeHelp.removeAttribute('aria-describedby');activeHelp=null;}
function showHelp(button,label,description){
 closeHelp();activeHelp=button;button.setAttribute('aria-describedby',tooltip.id);tooltip.replaceChildren(node('b',label),node('p',description));tooltip.hidden=false;
 const rect=button.getBoundingClientRect(),width=tooltip.offsetWidth,height=tooltip.offsetHeight;
 tooltip.style.left=Math.max(12,Math.min(rect.left,window.innerWidth-width-12))+'px';
 tooltip.style.top=Math.max(12,rect.bottom+height+12<window.innerHeight?rect.bottom+8:rect.top-height-8)+'px';
}
function helpLabel(label,key=label,context=''){
 const aliases={'Evaluable / planned cases':'Evaluable case coverage','Common evaluable cases':'Evaluable case coverage','Expected tools: name, arguments and turn':'Expected tools','Latency':'Full latency','Cost':'Total known cost'};
 key=aliases[key]||key.split(' · ')[0];const description=data.metric_help[key];
 if(!description)return node('span',label);
 const button=node('button',label,'metric-help');button.type='button';
 const show=()=>showHelp(button,label,description+(context?' '+context:''));
 button.addEventListener('mouseenter',show);button.addEventListener('focus',show);button.addEventListener('click',show);
 button.addEventListener('mouseleave',event=>{if(!tooltip.contains(event.relatedTarget)&&document.activeElement!==button)closeHelp();});
 button.addEventListener('blur',closeHelp);return button;
}
tooltip.addEventListener('mouseleave',()=>{if(document.activeElement!==activeHelp)closeHelp();});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeHelp();});
document.addEventListener('click',event=>{if(!tooltip.contains(event.target)&&!event.target.closest('.metric-help'))closeHelp();});
window.addEventListener('resize',closeHelp);document.addEventListener('scroll',event=>{if(event.target!==tooltip)closeHelp();},true);
const models=unique(records.map(r=>r.model)),ALL_PROJECTS='All projects',ALL_CASES='__all_scenarios__';let scope='project';
document.documentElement.style.setProperty('--model-count',Math.max(1,models.length));
const aggregate=()=>byId('case').value===ALL_CASES;
const projectLabel=()=>data.project_names[byId('project').value]||byId('project').value;
function node(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
function options(id,values,label=v=>v){byId(id).replaceChildren(...values.map(v=>{const o=node('option',label(v));o.value=v;return o;}));}
const status=s=>({ok:'complete',not_run:'not run',error:'execution error',quota_capacity:'quota blocked',empty_response:'empty response',output_limit:'truncated output',tool_iteration_limit:'tool iteration limit',call_limit:'call limit',skipped_context:'context exceeded',context_failed:'context exceeded'})[s]||s||'not run';
const number=n=>n===null||n===undefined?'—':Number(n).toLocaleString('en-US');
const seconds=n=>n===null||n===undefined?'—':Number(n).toFixed(2)+' s';
const usd=n=>n===null||n===undefined?'—':'USD '+Number(n).toFixed(4);
const percent=(a,b)=>b?Math.round(100*a/b)+'% ('+a+'/'+b+')':'—';
function selection(){return records.filter(r=>r.project===byId('project').value&&r.case===byId('case').value);}
function drawMetrics(){
 if(aggregate()){drawAggregate();return;}
 const selected=selection();
 const rows=scope==='case'?selected.map(r=>({...r.metrics,model:r.model,state:r.status})):(scope==='common'?data.common_rows:data.project_rows).filter(r=>r.project===byId('project').value);
 const fields=[
  [scope==='case'?'Scenario status':scope==='common'?'Common evaluable cases':'Evaluable / planned cases',r=>scope==='case'?status(r.state):r.evaluable+' / '+r.planned],
  ['Complete conversations',r=>percent(r.complete,r.evaluable)],
  ['Ordered flow milestones',r=>percent(r.paths_passed,r.paths_total)],
  ['Expected tools: name, arguments and turn',r=>percent(r.tool_checks_passed,r.tool_checks_total)],
  ['Explicit case rules',r=>percent(r.rules_passed,r.rules_total)],
  ['TTFT · median per call',r=>seconds(r.ttft_s)],
  ['First user-facing text · median per turn',r=>seconds(r.turn_first_text_s)],
  ['Full latency · median per call',r=>seconds(r.latency_s)],
  ['Flow latency · median per turn',r=>seconds(r.turn_latency_s)],
  ['Native / invalid function calls',r=>number(r.native_calls)+' / '+number(r.invalid_calls)],
  ['Language flags: confirmed / pending',r=>number(r.language_confirmed)+' / '+number(r.language_pending)],
  ['Largest accepted input · tokens',r=>number(r.max_input_tokens)],
  ['Total known cost · selected scope',r=>r.tested?usd(r.cost_usd):'—'],
  ['Provider or context rejections',r=>number(r.infrastructure_errors)]
 ];
 const table=byId('metrics');table.replaceChildren();const head=node('thead',''),hr=node('tr','');hr.append(node('th','Metric'));models.forEach(m=>hr.append(node('th',m)));head.append(hr);table.append(head);
 const body=node('tbody','');for(const [index,[label,format]] of fields.entries()){const tr=node('tr',''),cell=node('td','');cell.append(helpLabel(label,label,label.includes('median')?'This view shows the median (50th percentile) of recorded samples. '+(scope==='case'?'Selected-scenario timings include accepted calls and recorded turns from incomplete conversations.':'Project timings use complete conversations only.') : ''));tr.append(cell);for(const model of models){const r=rows.find(x=>x.model===model);tr.append(node('td',r&&(index===0||r.tested)?format(r):'—'));}body.append(tr);}table.append(body);
 byId('metric-title').textContent=scope==='case'?'Metrics for scenario '+byId('case').value:(scope==='common'?'Common cases · ':'Metrics for ')+(data.project_names[byId('project').value]||byId('project').value);
 byId('metric-note').textContent=(scope==='common'?'Only cases with evaluable observations from all selected models; quality failures are retained. ':scope==='project'?'Coverage may differ across models. Use “Same cases across all models” to compare a common selection. ':'')+'TTFT includes the first text or tool delta. '+(scope!=='case'?'Medians use complete conversations. ':'Medians use accepted calls and recorded turns, including incomplete conversations. ')+'Per-turn timings include orchestration and simulated tool responses. Quota waits are excluded. Milestones allow extra steps. Rules are explicit checks, not an exhaustive semantic evaluation. No language flag does not guarantee no leakage. Costs are calculated, not invoices; rejections are not scored as quality failures.';
 for(const value of ['project','common','case'])byId('scope-'+value).setAttribute('aria-pressed',scope===value);
}
function drawConversations(){
 if(data.shareable){byId('conversation-section').hidden=true;return;}
 const selected=selection(),panes=byId('panes');panes.replaceChildren();
 byId('turn-control').hidden=aggregate();
 if(aggregate()){byId('description').textContent='Select a project and a specific simulated scenario to inspect the selected models’ conversations side by side.';return;}
 for(const model of models){
  const r=selected.find(x=>x.model===model),root=node('article','','pane');root.append(node('h3',model));panes.append(root);
  if(data.model_profiles[model])root.append(node('p',data.model_profiles[model],'meta'));
  if(!r){root.append(node('p','No observation for this case.','empty'));continue;}
  root.append(node('p','Recorded: '+status(r.raw_status)+' · Reviewed: '+status(r.status),'meta'));
  if(r.attempt==='Follow-up')root.append(node('p','Follow-up attempt · Original outcome: '+status(r.original_status)+'. Original evidence retained in the baseline report.','meta'));
  const mini=node('div','','mini');for(const [label,value] of [['TTFT',seconds(r.metrics?.ttft_s)],['Latency',seconds(r.metrics?.latency_s)],['Cost',r.metrics?.tested?usd(r.metrics.cost_usd):'—']]){const box=node('div','');box.append(node('b',value),helpLabel(label,label,label==='Cost'?'':'Median of accepted calls in this scenario, including incomplete conversations.'));mini.append(box);}root.append(mini);
  if(r.adjudicated)root.append(node('p','Silent closing allowed by the prompt. Original history preserved.','meta'));
  if(!r.history.length)root.append(node('p','No messages recorded for this combination.','empty'));
  let turn=-1,visible=0;
  for(const m of r.history){
   if(m.role==='system')continue;if(m.role==='user')turn++;
   if(byId('turn').value!=='all'&&String(turn)!==byId('turn').value)continue;
   visible++;const item=node('div','','message '+m.role);
   item.append(node('div',(({user:'SIMULATED USER',assistant:'MODEL',tool:'SIMULATED TOOL RESULT'})[m.role]||m.role)+(turn>=0?' · TURN '+(turn+1):' · START'),'role'));
   if(m.content)item.append(node('div',typeof m.content==='string'?m.content:JSON.stringify(m.content,null,2),'content'));
   else if(!m.tool_calls?.length)item.append(node('div','No visible text.','empty'));
   if(m.tool_calls?.length){const d=node('details','');d.append(node('summary','Function calls ('+m.tool_calls.length+')'));d.append(node('pre',JSON.stringify(m.tool_calls,null,2)));item.append(d);}root.append(item);
  }
  if(r.history.length&&!visible)root.append(node('p','This model did not record the selected turn.','empty'));
 }
}
function draw(){closeHelp();drawMetrics();drawConversations();drawProfile();}
function statisticCell(r){
 const cell=node('td','');if(!r?.n){cell.textContent='—';return cell;}
 const f=v=>Number(v).toLocaleString('en-US',{minimumFractionDigits:r.unit==='USD'?5:2,maximumFractionDigits:r.unit==='USD'?5:2});
 cell.append(node('b',f(r.mean)),node('div',f(r.min)+'–'+f(r.max),'note'),node('div','N = '+r.n,'note'));return cell;
}
function tableHeader(id,columns){const table=byId(id);table.replaceChildren();const head=node('thead',''),hr=node('tr','');columns.forEach(c=>hr.append(node('th',c)));head.append(hr);table.append(head);const body=node('tbody','');table.append(body);return body;}
function metricLabel(row){const cell=node('td','');cell.append(helpLabel(row.metric+' ('+row.unit+')',row.metric,'Each cell shows arithmetic mean, minimum–maximum and sample count N. Sample: '+row.sample+'. Missing samples are excluded, not zero. Percentage checks use 0 for fail and 100 for pass. Timing uses complete conversations only, excluding local quota waits.'),node('div',row.sample,'note'));return cell;}
function drawAggregate(){
 const cohort=scope==='common'?'Same cases across all models':'Available cases';
 const rows=data.consolidated.filter(r=>r.project===byId('project').value&&(r.cohort===cohort||(scope==='common'&&r.cohort==='Same cases across all 4')));
 const body=tableHeader('metrics',['Metric / sample',...models]);
 for(const metric of unique(rows.map(r=>r.metric))){const sample=rows.find(r=>r.metric===metric),tr=node('tr','');tr.append(metricLabel(sample));models.forEach(model=>tr.append(statisticCell(rows.find(r=>r.metric===metric&&r.model===model))));body.append(tr);}
 if(!rows.length){const tr=node('tr',''),cell=node('td','No consolidated measurements available for this selection.');cell.colSpan=models.length+1;tr.append(cell);body.append(tr);}
 byId('metric-title').textContent=projectLabel()+' · All simulated scenarios';
 byId('metric-note').textContent=cohort+'. Each cell shows mean, min–max and sample count N, calculated from individual samples, not averages of project medians. Timing uses complete conversations and excludes quota waits. Quality includes evaluable failures; quota/context blocks are not quality failures. Input and known costs also include recorded calls from incomplete conversations. Missing values are not zero. Percentage checks are binary (0 = fail, 100 = pass).';
 for(const value of ['project','common','case'])byId('scope-'+value).setAttribute('aria-pressed',scope===value);
}
function drawProfile(){
 const baseline=data.baseline,model=byId('profile-model').value;
 byId('profile-comparison').hidden=!baseline.consolidated?.length;
 if(!baseline.consolidated?.length)return;
 const filter=r=>r.project===byId('project').value&&r.cohort==='Available cases'&&r.model===model;
 const initial=baseline.consolidated.filter(filter),current=data.consolidated.filter(filter);
 const before=baseline.model_profiles?.[model]||'Recorded initial profile',after=data.model_profiles[model]||'Recorded current profile';
 byId('profile-scope').textContent=projectLabel()+' · All simulated scenarios · '+model;
 byId('profile-context').textContent=data.profile_notes[model]||((before===after?'Same recorded reasoning profile in both selections. ':'')+'Initial and current selections are shown separately.');
 const body=tableHeader('profile-metrics',['Metric / sample','Initial · '+before,'Current · '+after,'Change in mean']);
 for(const metric of unique([...initial,...current].map(r=>r.metric))){const a=initial.find(r=>r.metric===metric),b=current.find(r=>r.metric===metric),tr=node('tr','');tr.append(metricLabel(b||a),statisticCell(a),statisticCell(b));
  const delta=a?.n&&b?.n?b.mean-a.mean:null;tr.append(node('td',delta===null?'—':(delta>0?'+':'')+delta.toFixed((b||a).unit==='USD'?5:2)));body.append(tr);}
}
function scenario(){
 scope=aggregate()?(scope==='common'?'common':'project'):'case';
 byId('scope-case').disabled=aggregate();
 const rows=selection(),n=Math.max(0,...rows.map(r=>r.history.filter(m=>m.role==='user').length));
 options('turn',['all',...Array.from({length:n},(_,i)=>String(i))],v=>v==='all'?'All turns':'Turn '+(Number(v)+1));byId('description').textContent=rows[0]?.description||'';draw();
}
function project(){
 const rows=records.filter(r=>r.project===byId('project').value);
 options('case',[ALL_CASES,...unique(rows.map(r=>r.case))],v=>v===ALL_CASES?'All simulated scenarios':v+(rows.find(r=>r.case===v)?.scenario_name?' · '+rows.find(r=>r.case===v).scenario_name:''));
 byId('case').disabled=byId('project').value===ALL_PROJECTS;
 byId('scope-project').textContent='All simulated scenarios';scenario();
}
options('project',[ALL_PROJECTS,...unique(records.map(r=>r.project))],v=>data.project_names[v]||v);
options('profile-model',models);
const changedModel=models.find(m=>data.baseline.model_profiles?.[m]&&data.baseline.model_profiles[m]!==data.model_profiles[m]);
if(changedModel)byId('profile-model').value=changedModel;
byId('profile-model').addEventListener('change',drawProfile);
byId('profile-description').textContent=Object.entries(data.model_profiles).map(([model,profile])=>model+': '+profile).join(' · ')+'.';
byId('followup-note').textContent=data.followup_count?data.followup_count+' user-requested follow-up attempts replace only their corresponding cases, regardless of success or failure. This is a targeted follow-up view; the first-attempt benchmark remains available separately.':'These are first-attempt benchmark results, with reviewed silent endings documented separately.';
if(data.historical_count)byId('followup-note').append(node('p',data.historical_count+' earlier-profile records are kept in the historical benchmark; reasoning profiles are not mixed in the current comparison.'));
if(data.followup_count||data.historical_count)byId('followup-note').append(node('p','Initial profile metrics are embedded above when available. Full original histories remain in a separate private archive.'));
if(data.shareable)byId('privacy-note').textContent='Shareable metrics report · Anonymous projects and scenarios. Prompts, conversations, tool arguments and private project names are not included. Tool responses were simulated. Works offline.';
byId('stamp').textContent=(data.synthetic?'SYNTHETIC OFFLINE RESULTS — not measured LLM performance. ':'')+'As of: '+data.generated_at+' · Unrun and blocked cases are shown explicitly.';
byId('project').addEventListener('change',project);byId('case').addEventListener('change',scenario);byId('turn').addEventListener('change',drawConversations);
for(const value of ['project','common','case'])byId('scope-'+value).addEventListener('click',()=>{scope=value;if(value!=='case')byId('case').value=ALL_CASES;scenario();});project();
// Enhance only after the interactive tables have rendered successfully. A disabled,
// stripped or failed script leaves the pre-rendered reading view fully accessible.
if(data.shareable){
 const reading=byId('static-report'),interactive=byId('interactive-report');
 byId('show-reading-view').hidden=false;byId('return-interactive').hidden=false;
 byId('show-reading-view').addEventListener('click',()=>{closeHelp();reading.hidden=false;interactive.hidden=true;window.scrollTo(0,0);});
 byId('return-interactive').addEventListener('click',()=>{reading.hidden=true;interactive.hidden=false;window.scrollTo(0,0);});
 interactive.hidden=false;reading.hidden=true;
}
</script></html>"""
