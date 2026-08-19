import { Archive, ChevronRight, Plus, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Empty, Loading, PageHead, RiskBadge, fmtDate } from '../components'
import type { Requirement } from '../types'

export default function Requirements(){
  const [items,setItems]=useState<Requirement[]>()
  const [q,setQ]=useState('')
  const [includeArchived,setIncludeArchived]=useState(false)
  useEffect(()=>{api.get<Requirement[]>('/requirements').then(setItems)},[])
  const shown=useMemo(()=>items?.filter(x=>(includeArchived||!x.archived)&&`#${x.sequence_id}${x.name}${x.owner.display_name}`.toLowerCase().includes(q.toLowerCase())),[items,q,includeArchived])
  if(!items)return <Loading/>
  return <>
    <PageHead eyebrow="需求" title="需求交付" description="交付节点跟随需求实例，进入详情即可看到完整状态。" action={<Link className="button primary" to="/requirements/new"><Plus size={16}/>新建需求</Link>}/>
    <div className="toolbar panel"><div className="search"><Search size={16}/><input placeholder="搜索 ID、名称或负责人" value={q} onChange={e=>setQ(e.target.value)}/></div><span>{shown?.length||0} 条需求</span><button className="button ghost compact" onClick={()=>setIncludeArchived(!includeArchived)}><Archive size={15}/>{includeArchived?'隐藏已归档':'显示已归档'}</button></div>
    {shown?.length?<div className="card-list">{shown.map(r=><Link className="req-card" to={`/requirements/${r.id}`} key={r.id}>
      <div className="req-card-top"><span className="key-chip">#{r.sequence_id}</span><RiskBadge level={r.risk_level}/></div>
      <h3>{r.name}</h3><p>{r.owner.display_name} · {r.template_name} · {r.target_version||'未设置版本'}</p>{r.ai_analysis&&<div className="req-ai-summary"><span>AI</span>{r.ai_analysis.summary}{r.ai_stale&&<small>待更新</small>}</div>}
      <div className="req-card-bottom"><div><small>当前节点</small><strong>{r.current_nodes.join('、')||'已完成'}</strong></div><div><small>计划完成</small><strong>{fmtDate(r.planned_completion)}</strong></div><div className="mini-progress"><span>{r.progress}%</span><i><b style={{width:`${r.progress}%`}}/></i></div><ChevronRight/></div>
    </Link>)}</div>:<Empty title="没有匹配的需求" detail="可以新建需求并从交付模板生成节点。"/>}
  </>
}
