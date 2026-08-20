import { ChevronRight, Plus, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Empty, Loading, PageHead, RiskBadge, fmtDate } from '../components'
import type { Requirement, RequirementLifecycleStatus } from '../types'

const lifecycleTabs:{value:RequirementLifecycleStatus;label:string}[]=[
  {value:'active',label:'进行中'},
  {value:'planned',label:'计划中'},
  {value:'archived',label:'已归档'},
]

export default function Requirements(){
  const [items,setItems]=useState<Requirement[]>()
  const [q,setQ]=useState('')
  const [statusFilter,setStatusFilter]=useState<RequirementLifecycleStatus>('active')
  useEffect(()=>{api.get<Requirement[]>('/requirements').then(setItems)},[])
  const shown=useMemo(()=>items?.filter(item=>item.lifecycle_status===statusFilter&&`#${item.sequence_id}${item.name}${item.owner.display_name}`.toLowerCase().includes(q.toLowerCase())),[items,q,statusFilter])
  if(!items)return <Loading/>
  return <>
    <PageHead eyebrow="需求" title="需求交付" description="交付节点跟随需求实例，进入详情即可看到完整状态。" action={<Link className="button primary" to="/requirements/new"><Plus size={16}/>新建需求</Link>}/>
    <div className="requirement-status-tabs panel">{lifecycleTabs.map(tab=><button type="button" className={statusFilter===tab.value?'active':''} onClick={()=>setStatusFilter(tab.value)} key={tab.value}><span>{tab.label}</span><b>{items.filter(item=>item.lifecycle_status===tab.value).length}</b></button>)}</div>
    <div className="toolbar panel"><div className="search"><Search size={16}/><input placeholder="搜索 ID、名称或负责人" value={q} onChange={event=>setQ(event.target.value)}/></div><span>{shown?.length||0} 条需求</span></div>
    {shown?.length?<div className="card-list">{shown.map(requirement=><Link className="req-card" to={`/requirements/${requirement.id}`} key={requirement.id}>
      <div className="req-card-top"><span className="key-chip">#{requirement.sequence_id}</span>{requirement.lifecycle_status==='active'?<RiskBadge level={requirement.risk_level}/>:<span className={`requirement-state-badge ${requirement.lifecycle_status}`}>{requirement.lifecycle_status==='planned'?'计划中':'已归档'}</span>}</div>
      <h3>{requirement.name}</h3><p>{requirement.owner.display_name} · {requirement.template_name} · {requirement.target_version||'未设置版本'}</p>{requirement.lifecycle_status==='active'&&requirement.ai_analysis&&<div className="req-ai-summary"><span>AI</span>{requirement.ai_analysis.summary}{requirement.ai_stale&&<small>待更新</small>}</div>}
      <div className="req-card-bottom"><div><small>当前节点</small><strong>{requirement.current_nodes.join('、')||'已完成'}</strong></div><div><small>计划完成</small><strong>{fmtDate(requirement.planned_completion)}</strong></div><div className="mini-progress"><span>{requirement.progress}%</span><i><b style={{width:`${requirement.progress}%`}}/></i></div><ChevronRight/></div>
    </Link>)}</div>:<Empty title={`没有${lifecycleTabs.find(tab=>tab.value===statusFilter)?.label}需求`} detail="可以新建需求，或在需求详情中调整需求状态。"/>}
  </>
}
