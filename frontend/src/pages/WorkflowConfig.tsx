import { Background,Controls,Handle,MarkerType,MiniMap,Position,ReactFlow,addEdge,useEdgesState,useNodesState,type Connection,type Edge,type Node } from '@xyflow/react'
import { Edit3,GitFork,Layers3,Link2,Plus,Save,Trash2,X } from 'lucide-react'
import { FormEvent,useCallback,useEffect,useMemo,useState } from 'react'
import { api } from '../api'
import { Empty,Field,Loading,PageHead,Toast } from '../components'
import type { Definition,Domain,Template,TemplateNode } from '../types'

type Tab='domains'|'definitions'|'templates'
const domainNames=(domains?:Domain[],fallback?:Domain)=>domains?.length?domains.map(domain=>domain.name).join('、'):fallback?.name||'未分组'
function PaletteNode({data}:{data:{node:TemplateNode}}){const n=data.node;return <div className="template-node" style={{'--domain':n.domain.color} as React.CSSProperties}><Handle type="target" position={Position.Left}/><span>{domainNames(n.domains,n.domain)}</span><strong>{n.name}</strong><small>{n.completion_criteria||'未设置完成标准'}</small><Handle type="source" position={Position.Right}/></div>}
const nodeTypes={template:PaletteNode}

export default function WorkflowConfig(){const [tab,setTab]=useState<Tab>('templates');return <><PageHead eyebrow="配置中心" title="通用交付配置" description="先定义交付领域和节点，再通过拖动与连线组成可复用的串并行模板。"/><div className="tabs"><button className={tab==='domains'?'active':''} onClick={()=>setTab('domains')}><Layers3/>交付领域</button><button className={tab==='definitions'?'active':''} onClick={()=>setTab('definitions')}><Link2/>通用节点</button><button className={tab==='templates'?'active':''} onClick={()=>setTab('templates')}><GitFork/>可视化模板</button></div>{tab==='domains'?<Domains/>:tab==='definitions'?<Definitions/>:<Templates/>}</>}

function Domains(){
  const [items,setItems]=useState<Domain[]>()
  const [editing,setEditing]=useState<Domain>()
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const load=()=>api.get<Domain[]>('/domains').then(setItems)
  useEffect(()=>{void load()},[])
  async function add(e:FormEvent<HTMLFormElement>){
    e.preventDefault()
    const fd=new FormData(e.currentTarget)
    try{await api.post('/domains',Object.fromEntries(fd));e.currentTarget.reset();setMessage('交付领域已创建');setError('');await load()}
    catch(err){setError((err as Error).message)}
  }
  async function save(e:FormEvent<HTMLFormElement>){
    e.preventDefault()
    if(!editing)return
    const fd=new FormData(e.currentTarget)
    const body={...Object.fromEntries(fd),active:fd.get('active')==='on'}
    try{await api.patch(`/domains/${editing.id}`,body);setEditing(undefined);setMessage('交付领域已更新');setError('');await load()}
    catch(err){setError((err as Error).message)}
  }
  async function deactivate(domain:Domain){
    if(!window.confirm(`停用领域「${domain.name}」？已有需求和节点快照不会被删除。`))return
    try{await api.del(`/domains/${domain.id}`);setEditing(undefined);setMessage('交付领域已停用');setError('');await load()}
    catch(err){setError((err as Error).message)}
  }
  if(!items)return <Loading/>
  return <div className="config-split"><form className="panel config-form" onSubmit={editing?save:add}><div className="form-title-row"><div><h2>{editing?'编辑交付领域':'新建交付领域'}</h2><p>领域用于为节点分类和着色。</p></div>{editing&&<button type="button" className="icon" onClick={()=>setEditing(undefined)}><X size={16}/></button>}</div><Field label="领域名称"><input name="name" required placeholder="例如：设计" defaultValue={editing?.name||''} key={`domain-name-${editing?.id||'new'}`}/></Field><Field label="标识颜色"><input name="color" type="color" defaultValue={editing?.color||'#2f7d57'} key={`domain-color-${editing?.id||'new'}`}/></Field><Field label="说明"><textarea name="description" defaultValue={editing?.description||''} key={`domain-desc-${editing?.id||'new'}`}/></Field>{editing&&<label className="check"><input name="active" type="checkbox" defaultChecked={editing.active}/>使用中</label>}<button className="button primary">{editing?<><Save size={16}/>保存修改</>:<><Plus size={16}/>创建领域</>}</button></form><div className="panel config-list"><div className="section-head"><div><h2>已有领域</h2><p>{items.length} 个领域</p></div></div>{items.map(d=><div className="domain-row editable" key={d.id}><i style={{background:d.color}}/><div><strong>{d.name}</strong><span>{d.description||'暂无说明'}</span></div><b>{d.active?'使用中':'已停用'}</b><div className="row-actions"><button className="icon" onClick={()=>setEditing(d)}><Edit3 size={15}/></button><button className="icon" onClick={()=>deactivate(d)} disabled={!d.active}><Trash2 size={15}/></button></div></div>)}</div>{message&&<Toast text={message}/>} {error&&<Toast text={error} type="error"/>}</div>
}

function Definitions(){
  const [items,setItems]=useState<Definition[]>()
  const [domains,setDomains]=useState<Domain[]>([])
  const [editing,setEditing]=useState<Definition>()
  const [domainTab,setDomainTab]=useState<string>('all')
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const load=()=>Promise.all([api.get<Definition[]>('/node-definitions'),api.get<Domain[]>('/domains')]).then(([d,ds])=>{setItems(d);setDomains(ds)})
  useEffect(()=>{void load()},[])
  async function add(e:FormEvent<HTMLFormElement>){
    e.preventDefault()
    const fd=new FormData(e.currentTarget)
    const body={...Object.fromEntries(fd),domain_ids:fd.getAll('domain_ids').map(String)}
    if(!body.domain_ids.length){setError('请至少选择一个所属领域');return}
    try{await api.post('/node-definitions',body);e.currentTarget.reset();setMessage('通用节点已创建');setError('');await load()}
    catch(err){setError((err as Error).message)}
  }
  async function save(e:FormEvent<HTMLFormElement>){
    e.preventDefault()
    if(!editing)return
    const fd=new FormData(e.currentTarget)
    const body={...Object.fromEntries(fd),domain_ids:fd.getAll('domain_ids').map(String),active:fd.get('active')==='on'}
    if(!body.domain_ids.length){setError('请至少选择一个所属领域');return}
    try{await api.patch(`/node-definitions/${editing.id}`,body);setEditing(undefined);setMessage('通用节点已更新');setError('');await load()}
    catch(err){setError((err as Error).message)}
  }
  async function deactivate(definition:Definition){
    if(!window.confirm(`停用环节「${definition.name}」？已有需求快照不会被删除。`))return
    try{await api.del(`/node-definitions/${definition.id}`);setEditing(undefined);setMessage('通用节点已停用');setError('');await load()}
    catch(err){setError((err as Error).message)}
  }
  if(!items)return <Loading/>
  const selectedDomainIds=editing?.domain_ids?.length?editing.domain_ids:[editing?.domain_id||''].filter(Boolean)
  const visibleItems=domainTab==='all'?items:items.filter(item=>(item.domain_ids?.length?item.domain_ids:[item.domain_id]).includes(domainTab))
  const domainTabs=domains.filter(domain=>items.some(item=>(item.domain_ids?.length?item.domain_ids:[item.domain_id]).includes(domain.id)))
  return <div className="config-split"><form className="panel config-form" onSubmit={editing?save:add}><div className="form-title-row"><div><h2>{editing?'编辑通用节点':'新建通用节点'}</h2><p>节点只描述交付动作与完成标准，不预设耗时。</p></div>{editing&&<button type="button" className="icon" onClick={()=>setEditing(undefined)}><X size={16}/></button>}</div><Field label="节点名称"><input name="name" required placeholder="例如：视觉验收" defaultValue={editing?.name||''} key={`def-name-${editing?.id||'new'}`}/></Field><Field label="所属领域" hint="可多选；第一个勾选的领域会作为画布主色"><div className="checkbox-list" key={`def-domain-${editing?.id||'new'}`}>{domains.filter(d=>d.active||selectedDomainIds.includes(d.id)).map(d=><label key={d.id}><input name="domain_ids" type="checkbox" value={d.id} defaultChecked={selectedDomainIds.includes(d.id)}/><i style={{background:d.color}}/>{d.name}</label>)}</div></Field><Field label="说明"><textarea name="description" defaultValue={editing?.description||''} key={`def-desc-${editing?.id||'new'}`}/></Field><Field label="完成标准"><textarea name="completion_criteria" placeholder="怎样算这个节点完成" defaultValue={editing?.completion_criteria||''} key={`def-criteria-${editing?.id||'new'}`}/></Field>{editing&&<label className="check"><input name="active" type="checkbox" defaultChecked={editing.active}/>使用中</label>}<button className="button primary">{editing?<><Save size={16}/>保存修改</>:<><Plus size={16}/>创建节点</>}</button></form><div className="panel config-list"><div className="section-head"><div><h2>节点库</h2><p>按领域管理通用交付环节</p></div></div><div className="domain-tabs"><button className={domainTab==='all'?'active':''} onClick={()=>setDomainTab('all')}>全部</button>{domainTabs.map(domain=><button className={domainTab===domain.id?'active':''} key={domain.id} onClick={()=>setDomainTab(domain.id)}><i style={{background:domain.color}}/>{domain.name}</button>)}</div>{visibleItems.map(n=><div className="definition-row editable" key={n.id}><i style={{background:n.domain.color}}/><div><span>{domainNames(n.domains,n.domain)}{n.active?'':' · 已停用'}</span><strong>{n.name}</strong><small>{n.completion_criteria||'未设置完成标准'}</small></div><div className="row-actions"><button className="icon" onClick={()=>setEditing(n)}><Edit3 size={15}/></button><button className="icon" onClick={()=>deactivate(n)} disabled={!n.active}><Trash2 size={15}/></button></div></div>)}</div>{message&&<Toast text={message}/>} {error&&<Toast text={error} type="error"/>}</div>
}

function Templates(){
  const [templates,setTemplates]=useState<Template[]>()
  const [definitions,setDefinitions]=useState<Definition[]>([])
  const [selected,setSelected]=useState<string>()
  const [detail,setDetail]=useState<Template>()
  const [editingTemplate,setEditingTemplate]=useState(false)
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const [nodes,setNodes,onNodesChange]=useNodesState<Node>([])
  const [edges,setEdges,onEdgesChange]=useEdgesState<Edge>([])
  const loadLists=useCallback(()=>Promise.all([api.get<Template[]>('/templates'),api.get<Definition[]>('/node-definitions')]).then(([t,d])=>{setTemplates(t);setDefinitions(d);if(!selected&&t.length)setSelected(t[0].id)}),[selected])
  useEffect(()=>{void loadLists()},[loadLists])
  const loadDetail=useCallback(()=>{if(selected)api.get<Template>(`/templates/${selected}`).then(t=>{setDetail(t);setNodes((t.nodes||[]).map(n=>({id:n.id,type:'template',position:n.position,data:{node:n}})));setEdges((t.edges||[]).map(e=>({id:e.id,source:e.source,target:e.target,markerEnd:{type:MarkerType.ArrowClosed},style:{stroke:'#6f8378',strokeWidth:2}})))})},[selected,setNodes,setEdges])
  useEffect(()=>{setEditingTemplate(false);void loadDetail()},[loadDetail])
  const refreshTemplate=useCallback(async()=>{await Promise.all([loadDetail(),loadLists()])},[loadDetail,loadLists])
  const unused=useMemo(()=>definitions.filter(d=>d.active&&(d.domains||[d.domain]).some(domain=>!detail?.nodes?.some(n=>n.definition_id===d.id&&n.domain_id===domain.id))),[definitions,detail])
  async function create(e:FormEvent<HTMLFormElement>){e.preventDefault();const fd=new FormData(e.currentTarget);try{const t=await api.post<Template>('/templates',Object.fromEntries(fd));setSelected(t.id);e.currentTarget.reset();setMessage('模板已创建');setError('');await loadLists()}catch(err){setError((err as Error).message)}}
  async function saveTemplate(e:FormEvent<HTMLFormElement>){e.preventDefault();if(!detail)return;const fd=new FormData(e.currentTarget);const body={...Object.fromEntries(fd),active:fd.get('active')==='on'};try{const updated=await api.patch<Template>(`/templates/${detail.id}`,body);setDetail(current=>current?{...current,...updated}:updated);setEditingTemplate(false);setMessage('模板已更新');setError('');await loadLists()}catch(err){setError((err as Error).message)}}
  async function addNode(definition:Definition){if(!selected)return;const existing=new Set((detail?.nodes||[]).filter(n=>n.definition_id===definition.id).map(n=>n.domain_id));const domains=(definition.domains||[definition.domain]).filter(domain=>!existing.has(domain.id));try{await Promise.all(domains.map((domain,index)=>api.post(`/templates/${selected}/nodes`,{definition_id:definition.id,domain_id:domain.id,position_x:120+((nodes.length+index)%3)*260,position_y:90+Math.floor((nodes.length+index)/3)*180})));setMessage(domains.length>1?'节点已按领域加入画布':'节点已加入画布');setError('');await refreshTemplate()}catch(err){setError((err as Error).message)}}
  const connect=useCallback(async(c:Connection)=>{if(!selected||!c.source||!c.target)return;try{const edge=await api.post<{id:string;source:string;target:string}>(`/templates/${selected}/edges`,{source:c.source,target:c.target});setEdges(es=>addEdge({...c,id:edge.id,markerEnd:{type:MarkerType.ArrowClosed}},es));setMessage('依赖关系已保存');setError('');await loadLists()}catch(err){setError((err as Error).message)}},[selected,setEdges,loadLists])
  async function moved(_:unknown,node:Node){try{await api.patch(`/template-nodes/${node.id}`,{position_x:node.position.x,position_y:node.position.y});setDetail(current=>current?{...current,nodes:current.nodes?.map(n=>n.id===node.id?{...n,position:node.position}:n)}:current)}catch(err){setError((err as Error).message);await loadDetail()}}
  async function deleteSelected(){const selectedNodes=nodes.filter(n=>n.selected);const selectedEdges=edges.filter(e=>e.selected);try{await Promise.all([...selectedNodes.map(n=>api.del(`/template-nodes/${n.id}`)),...selectedEdges.map(e=>api.del(`/template-edges/${e.id}`))]);setMessage('已从模板移除');setError('');await refreshTemplate()}catch(err){setError((err as Error).message)}}
  if(!templates)return <Loading/>
  return <div className="template-layout"><aside className="panel template-sidebar"><form onSubmit={create}><h3>模板</h3><div className="inline-create"><input name="name" required placeholder="新模板名称"/><button className="icon"><Plus/></button></div></form><div className="template-list">{templates.map(t=><button className={selected===t.id?'active':''} key={t.id} onClick={()=>setSelected(t.id)}><strong>{t.name}</strong><span>{t.node_count} 节点 · {t.edge_count} 连线</span></button>)}</div><hr/><h3>可用节点</h3><p className="sidebar-hint">点击添加到当前模板</p><div className="palette">{unused.map(d=><button onClick={()=>addNode(d)} key={d.id}><i style={{background:d.domain.color}}/><span><strong>{d.name}</strong><small>{d.domain.name}</small></span><Plus/></button>)}</div></aside><section className="panel template-workspace"><div className="section-head"><div><h2>{detail?.name||'选择一个模板'}</h2><p>拖动排布节点，从节点手柄连线；分叉即并行，汇合后继续串行。</p></div><div className="section-actions"><button className="button ghost compact" onClick={()=>setEditingTemplate(true)} disabled={!detail}><Edit3 size={15}/>编辑模板</button><button className="button danger compact" onClick={deleteSelected} disabled={!nodes.some(n=>n.selected)&&!edges.some(e=>e.selected)}><Trash2 size={15}/>删除所选</button></div></div>{editingTemplate&&detail&&<form className="template-edit-form" onSubmit={saveTemplate}><Field label="模板名称"><input name="name" required defaultValue={detail.name}/></Field><Field label="说明"><input name="description" defaultValue={detail.description}/></Field><label className="check"><input name="active" type="checkbox" defaultChecked={detail.active}/>使用中</label><div className="inline-actions"><button type="button" className="button ghost compact" onClick={()=>setEditingTemplate(false)}>取消</button><button className="button primary compact"><Save size={15}/>保存模板</button></div></form>}{selected?<div className="flow-canvas template-flow"><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={connect} onNodeDragStop={moved} fitView deleteKeyCode={null}><Background gap={22} size={1}/><Controls/><MiniMap/></ReactFlow><div className="canvas-legend"><span><i className="serial"/>串行：依次连接</span><span><i className="parallel"/>并行：从同一节点分叉</span><span>禁止形成循环依赖</span></div></div>:<Empty title="还没有模板" detail="先在左侧创建一个模板。"/>}</section>{message&&<Toast text={message}/>} {error&&<Toast text={error} type="error"/>}</div>
}
