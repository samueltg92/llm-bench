"""Offline private comparison of the exact conversation histories."""

import json

from .privacy import write_private


def render_review(records, path):
    # Model output is untrusted. Escape the data block and use textContent only.
    payload = json.dumps(records, ensure_ascii=False).replace("<", "\\u003c")
    document = TEMPLATE.replace("__RECORDS__", payload)
    write_private(path, document, plain=True)


TEMPLATE = """<!doctype html>
<html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Revisión privada de conversaciones</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f2f5f8;color:#142b42;font:15px system-ui,sans-serif}
header{padding:25px 4vw;background:#142b42;color:white}h1{font-size:25px;margin:0 0 8px}
header p{margin:4px 0;color:#d9e4ec}main{padding:20px 4vw}label{display:inline-grid;gap:6px;margin:0 18px 12px 0}
select{font:inherit;padding:9px;border:1px solid #cad5df;border-radius:6px;background:white;max-width:100%}
.panes{display:grid;grid-template-columns:1fr 1fr;gap:20px}.pane{min-width:0;background:white;border-radius:10px;padding:18px}
h2{font-size:18px;margin:0 0 10px}.meta{color:#536575;font-size:13px;padding-bottom:14px;border-bottom:1px solid #dde5ed}
.message{margin:16px 0;padding:12px;border-radius:7px;background:#f2f5f8;overflow-wrap:anywhere}
.assistant{background:#eaf7f5}.role{font-size:12px;font-weight:700;color:#087f8c;margin-bottom:6px}
.content{white-space:pre-wrap;line-height:1.5}details{margin-top:8px}summary{cursor:pointer}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px ui-monospace,monospace}.empty{color:#657889;font-style:italic}
@media(max-width:850px){.panes{grid-template-columns:1fr}}@media print{header,main{padding:12px}.panes{display:block}.pane{break-before:page}}
</style>
<header><h1>Revisión privada de conversaciones</h1>
<p>Preguntas, respuestas y function calls originales. Este archivo contiene contenido confidencial: no pertenece al repositorio público.</p>
<p>Funciona sin conexión. Los resultados de herramientas son simulados; no son consultas a servicios reales.</p></header>
<main><label>Proyecto<select id="project"></select></label><label>Caso<select id="case"></select></label>
<div class="panes"><section class="pane"><label>Modelo izquierdo<select id="left"></select></label><div id="a"></div></section>
<section class="pane"><label>Modelo derecho<select id="right"></select></label><div id="b"></div></section></div></main>
<script type="application/json" id="records">__RECORDS__</script>
<script>
const records=JSON.parse(document.getElementById('records').textContent);
const byId=id=>document.getElementById(id);
function node(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
function options(id,values){byId(id).replaceChildren(...values.map(v=>{const o=node('option',v);o.value=v;return o;}));}
const unique=xs=>[...new Set(xs)];
const status=s=>({ok:'completa',not_run:'sin ejecutar',error:'error de ejecución',quota_capacity:'bloqueada por cuota',empty_response:'respuesta vacía',output_limit:'salida truncada',tool_iteration_limit:'límite de iteraciones de tools',call_limit:'límite de llamadas',skipped_context:'no cabe en contexto',context_failed:'no cabe en contexto'})[s]||s;
options('project',unique(records.map(r=>r.project)));options('left',unique(records.map(r=>r.model)));
options('right',unique(records.map(r=>r.model)));if(byId('right').options.length>1)byId('right').selectedIndex=1;
function cases(){options('case',unique(records.filter(r=>r.project===byId('project').value).map(r=>r.case)));draw();}
function pane(target,model){
 const root=byId(target);root.replaceChildren();
 const r=records.find(r=>r.project===byId('project').value&&r.case===byId('case').value&&r.model===model);
 if(!r){root.append(node('p','No hay una observación de este modelo para el caso seleccionado.','empty'));return;}
 root.append(node('h2',r.case));
 root.append(node('p','Estado registrado: '+status(r.raw_status)+' · Estado revisado: '+status(r.status)+' · Ruta esperada: '+(r.path_match===null?'no aplica':r.path_match?'sí':'no'),'meta'));
 if(!r.history.length)root.append(node('p','No hay mensajes registrados para esta combinación.','empty'));
 if(r.adjudicated)root.append(node('p','Se reconoció un cierre silencioso permitido por el prompt. El historial siguiente permanece intacto.','meta'));
 for(const m of r.history){
  if(m.role==='system')continue;
  const item=node('div','','message '+m.role);
  item.append(node('div',({user:'USUARIO SIMULADO',assistant:'MODELO',tool:'RESULTADO SIMULADO DE TOOL'})[m.role]||m.role,'role'));
  if(m.content)item.append(node('div',typeof m.content==='string'?m.content:JSON.stringify(m.content,null,2),'content'));
  else if(!m.tool_calls?.length)item.append(node('div','Sin texto visible.','empty'));
  if(m.tool_calls?.length){const d=node('details','');d.append(node('summary','Function calls ('+m.tool_calls.length+')'));d.append(node('pre',JSON.stringify(m.tool_calls,null,2)));item.append(d);}
  root.append(item);
 }
}
function draw(){pane('a',byId('left').value);pane('b',byId('right').value);}
byId('project').addEventListener('change',cases);for(const id of ['case','left','right'])byId(id).addEventListener('change',draw);cases();
</script></html>"""
