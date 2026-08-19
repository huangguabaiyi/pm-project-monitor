import { ArrowRight, GitBranch, ListChecks, ShieldCheck, Siren } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Loading, PageHead, RiskBadge, fmtDate } from '../components'
import type { Job, Requirement } from '../types'

type Summary={counts:{requirements:number;normal:number;warning:number;severe:number;active_nodes:number};requirements:Requirement[];jobs:Job[]}
export default function Overview(){
  const [data,setData]=useState<Summary>()
  useEffect(()=>{api.get<Summary>('/dashboard').then(setData)},[])
  if(!data)return <Loading/>
  const cards=[['需求总数',data.counts.requirements,ListChecks,'ink'],['正常推进',data.counts.normal,ShieldCheck,'green'],['需要关注',data.counts.warning,GitBranch,'amber'],['高风险',data.counts.severe,Siren,'red']] as const
  return <>
    <PageHead eyebrow="工作台" title="交付全景" description="风险只由节点计划、实际状态和依赖关系计算，规则更少，信号更明确。"/>
    <div className="metric-grid">{cards.map(([label,value,Icon,tone])=><div className={`metric ${tone}`} key={label}><div><span>{label}</span><strong>{value}</strong></div><Icon/></div>)}</div>
    <section className="panel"><div className="section-head"><div><h2>重点需求</h2><p>按最近更新展示节点进度与计划信号</p></div><Link to="/requirements">查看全部 <ArrowRight size={15}/></Link></div>
      <div className="requirement-list">{data.requirements.map(r=><Link className="requirement-row" to={`/requirements/${r.id}`} key={r.id}>
        <div className="req-key">#{r.sequence_id}</div><div className="req-main"><strong>{r.name}</strong><span>{r.owner.display_name} · {r.template_name}</span></div><div className="req-current"><small>当前节点</small><span>{r.current_nodes.join('、')||'已完成'}</span></div><div className="progress-cell"><span>{r.completed_nodes}/{r.total_nodes}</span><div className="progress"><i style={{width:`${r.progress}%`}}/></div></div><div className="req-date"><small>计划完成</small><span>{fmtDate(r.planned_completion)}</span></div><RiskBadge level={r.risk_level}/>
      </Link>)}</div>
    </section>
  </>
}
