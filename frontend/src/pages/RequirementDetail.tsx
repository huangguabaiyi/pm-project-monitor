import { ArrowLeft, ChevronDown, ChevronUp, ExternalLink, FileText, PanelsTopLeft, RefreshCw, Save, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, type Edge, type Node } from '@xyflow/react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { dateInput, Empty, Field, Loading, PageHead, RiskBadge, Toast, fmtDate } from '../components'
import type { AIAnalysis, Person, Requirement, RequirementNode } from '../types'

const statuses:{value:string;label:string}[]=[{value:'not_started',label:'未开始'},{value:'in_progress',label:'进行中'},{value:'blocked',label:'受阻'},{value:'completed',label:'已完成'},{value:'skipped',label:'已跳过'}]
const statusLabel=(s:string)=>statuses.find(x=>x.value===s)?.label||s

function ScheduleNode({data}:{data:{node:RequirementNode}}){
  const n=data.node
  return <div className={`schedule-node status-${n.status} risk-${n.risk_level}`} style={{'--domain':n.domain_color} as React.CSSProperties}><Handle type="target" position={Position.Left}/><div className="node-domain">{n.domain_name}</div><strong>{n.name}</strong><span>{statusLabel(n.status)}</span>{n.risk_reasons.length>0&&<small>{n.risk_reasons[0]}</small>}<Handle type="source" position={Position.Right}/></div>
}
const nodeTypes={schedule:ScheduleNode}

export default function RequirementDetail(){
  const {id}=useParams()
  const [item,setItem]=useState<Requirement>()
  const [people,setPeople]=useState<Person[]>([])
  const [selected,setSelected]=useState<string>()
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const [analyzing,setAnalyzing]=useState(false)
  const load=useCallback(()=>{if(id)api.get<Requirement>(`/requirements/${id}`).then(setItem)},[id])
  useEffect(()=>{load();api.get<Person[]>('/people').then(setPeople)},[load])
  const nodes=useMemo<Node[]>(()=>item?.nodes?.map(n=>({id:n.id,type:'schedule',position:n.position,data:{node:n}}))||[],[item])
  const edges=useMemo<Edge[]>(()=>item?.edges?.map(e=>({id:e.id,source:e.source,target:e.target,markerEnd:{type:MarkerType.ArrowClosed},animated:true,style:{stroke:'#8fa097'}}))||[],[item])
  if(!item)return <Loading/>
  const requirementId=item.id
  const selectedNode=item.nodes?.find(n=>n.id===selected)
  async function saveNode(e:React.FormEvent<HTMLFormElement>){e.preventDefault();if(!selectedNode)return;const fd=new FormData(e.currentTarget);const body={planned_start:fd.get('planned_start')||null,planned_end:fd.get('planned_end')||null,status:fd.get('status'),notes:fd.get('notes'),blocked_reason:fd.get('blocked_reason'),owner_ids:fd.getAll('owner_ids').map(String)};try{await api.patch(`/requirement-nodes/${selectedNode.id}`,body);setMessage('节点计划已保存，AI 结论已标记为待更新');setError('');load()}catch(err){setError((err as Error).message)}}
  async function analyze(){setAnalyzing(true);setError('');try{const result=await api.post<Requirement>(`/requirements/${requirementId}/ai-analysis`,{});setItem(result);setMessage('AI 风险分析已更新')}catch(err){setError((err as Error).message)}finally{setAnalyzing(false)}}
  return <>
    <PageHead eyebrow={`需求 #${item.sequence_id}`} title={item.name} description={`${item.owner.display_name} · ${item.template_name} · ${item.target_version||'未设置版本'}`} action={<Link className="button ghost" to="/requirements"><ArrowLeft size={16}/>返回需求</Link>}/>
    <div className="detail-summary panel"><div><small>整体风险</small><RiskBadge level={item.risk_level}/></div><div><small>交付进度</small><strong>{item.completed_nodes}/{item.total_nodes} 节点</strong></div><div><small>当前节点</small><strong>{item.current_nodes.join('、')||'已完成'}</strong></div><div><small>计划完成</small><strong>{fmtDate(item.planned_completion)}</strong></div></div>
    {(item.meego_url||item.requirement_url||item.figma_url)&&<div className="resource-links">{item.meego_url&&<a href={item.meego_url} target="_blank" rel="noreferrer"><PanelsTopLeft/>Meego<ExternalLink/></a>}{item.requirement_url&&<a href={item.requirement_url} target="_blank" rel="noreferrer"><FileText/>需求文档<ExternalLink/></a>}{item.figma_url&&<a href={item.figma_url} target="_blank" rel="noreferrer"><span className="figma-mark">F</span>Figma<ExternalLink/></a>}</div>}
    {item.risk_reasons.length>0&&<div className={`risk-callout risk-${item.schedule_risk_level}`}><strong>计划风险提示</strong>{item.risk_reasons.map((r,i)=><span key={i}>· {r}</span>)}</div>}
    <AIAnalysisCard analysis={item.ai_analysis} analyzedAt={item.ai_analyzed_at} stale={item.ai_stale} error={item.ai_error} analyzing={analyzing} onAnalyze={analyze}/>
    <section className="panel flow-section"><div className="section-head"><div><h2>交付节点全景</h2><p>节点自动跟随需求；串行与并行关系来自创建时使用的模板。</p></div><span className="hint">点击节点编辑计划</span></div><div className="flow-canvas requirement-flow"><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={.45} maxZoom={1.5} nodesDraggable={false} onNodeClick={(_,n)=>setSelected(n.id)}><Background gap={22} size={1}/><Controls/><MiniMap nodeColor={n=>(n.data.node as RequirementNode).domain_color}/></ReactFlow></div></section>
    <section className="panel"><div className="section-head"><div><h2>节点计划与状态</h2><p>规则风险依据预期时间、实际状态与前后依赖；备注同时会进入 AI 综合分析。</p></div></div><div className="node-table"><div className="node-table-head"><span>节点</span><span>负责人</span><span>预期开始</span><span>预期结束</span><span>状态</span><span>风险</span><span/></div>{item.nodes?.map(n=><div key={n.id} className={selected===n.id?'node-table-wrap selected':'node-table-wrap'}><button className="node-row" onClick={()=>setSelected(selected===n.id?undefined:n.id)}><span><i style={{background:n.domain_color}}/>{n.name}<small>{n.domain_name}</small></span><span>{n.owners.map(x=>x.display_name).join('、')||'未设置'}</span><span>{fmtDate(n.planned_start)}</span><span>{fmtDate(n.planned_end)}</span><span><b className={`status-pill ${n.status}`}>{statusLabel(n.status)}</b></span><span><RiskBadge level={n.risk_level}/></span><span>{selected===n.id?<ChevronUp/>:<ChevronDown/>}</span></button>{selected===n.id&&<NodeEditor node={n} people={people} onSave={saveNode}/>}</div>)}</div></section>
    {!item.nodes?.length&&<Empty title="没有交付节点" detail="当前需求未生成节点。"/>}{message&&<Toast text={message}/>} {error&&<Toast text={error} type="error"/>}
  </>
}

function AIAnalysisCard({analysis,analyzedAt,stale,error,analyzing,onAnalyze}:{analysis?:AIAnalysis;analyzedAt?:string;stale:boolean;error?:string;analyzing:boolean;onAnalyze:()=>void}){
  return <section className="panel ai-result-card"><div className="ai-result-head"><div><Sparkles/><span><strong>AI 综合风险分析</strong><small>{analysis?`${fmtDate(analyzedAt,true)}${stale?' · 需求已变化，建议重新分析':''}`:'尚未生成 AI 结论'}</small></span></div><button className="button ghost compact" onClick={onAnalyze} disabled={analyzing}>{analyzing?<RefreshCw className="spin"/>:<Sparkles/>}{analyzing?'分析中…':analysis?'重新分析':'立即分析'}</button></div>{analysis?<div className="ai-result-body"><div className={`ai-summary ai-${analysis.risk_level}`}><strong>{analysis.summary}</strong><span>置信度 {Math.round(analysis.confidence*100)}% · {analysis.delivery_forecast.reason}</span></div>{analysis.signals.length>0&&<div className="ai-result-section"><h4>风险信号</h4>{analysis.signals.map((signal,index)=><div className="ai-signal" key={`${signal.node_id}-${index}`}><b className={`ai-dot ${signal.risk_level}`}/><span><strong>{signal.node_name}</strong><small>{signal.reason}</small>{signal.evidence.length>0&&<em>{signal.evidence.join('；')}</em>}</span></div>)}</div>}{analysis.actions.length>0&&<div className="ai-result-section"><h4>建议动作</h4>{analysis.actions.map((action,index)=><div className="ai-action" key={index}><b>{action.priority==='high'?'高':action.priority==='medium'?'中':'低'}</b><span>{action.action}<small>{action.owner_hint}</small></span></div>)}</div>}{analysis.missing_information.length>0&&<p className="ai-missing">待补充：{analysis.missing_information.join('、')}</p>}</div>:<div className="ai-empty">开启 AI 配置后，可综合分析节点备注、排期、状态、人员和依赖信息。</div>}{error&&<div className="ai-inline-error">上次分析失败：{error}</div>}</section>
}

function NodeEditor({node,people,onSave}:{node:RequirementNode;people:Person[];onSave:(e:React.FormEvent<HTMLFormElement>)=>void}){
  return <form className="node-editor" onSubmit={onSave}><Field label="预期开始"><input type="datetime-local" name="planned_start" defaultValue={dateInput(node.planned_start)}/></Field><Field label="预期结束"><input type="datetime-local" name="planned_end" defaultValue={dateInput(node.planned_end)}/></Field><Field label="状态"><select name="status" defaultValue={node.status}>{statuses.map(s=><option value={s.value} key={s.value}>{s.label}</option>)}</select></Field><Field label="节点负责人"><select name="owner_ids" multiple defaultValue={node.owners.map(x=>x.id)}>{people.filter(p=>p.active).map(p=><option key={p.id} value={p.id}>{p.display_name} · {p.role_name}</option>)}</select></Field><Field label="受阻原因"><input name="blocked_reason" defaultValue={node.blocked_reason||''} placeholder="仅状态为受阻时填写"/></Field><Field label="节点备注"><input name="notes" defaultValue={node.notes||''}/></Field><div className="node-editor-action"><button className="button primary compact"><Save size={15}/>保存节点</button></div></form>
}
