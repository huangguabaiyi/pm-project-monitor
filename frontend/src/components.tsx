import { AlertTriangle, CheckCircle2, Circle, Clock3, LoaderCircle } from 'lucide-react'
import type { ReactNode } from 'react'

export const riskMeta = {
  0:{label:'正常',className:'normal'}, 1:{label:'关注',className:'warning'}, 2:{label:'高风险',className:'severe'}
} as const

export function RiskBadge({level}:{level:0|1|2}){ const meta=riskMeta[level]; return <span className={`risk-badge ${meta.className}`}>{level===2?<AlertTriangle size={13}/>:level===1?<Clock3 size={13}/>:<CheckCircle2 size={13}/>} {meta.label}</span> }
export function PageHead({eyebrow,title,description,action}:{eyebrow?:string;title:string;description?:string;action?:ReactNode}){return <div className="page-head"><div>{eyebrow&&<div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description&&<p>{description}</p>}</div>{action}</div>}
export function Empty({title,detail}:{title:string;detail:string}){return <div className="empty"><Circle/><strong>{title}</strong><span>{detail}</span></div>}
export function Loading(){return <div className="loading"><LoaderCircle className="spin"/>正在加载…</div>}
export function Field({label,children,hint}:{label:string;children:ReactNode;hint?:string}){return <label className="field"><span>{label}</span>{children}{hint&&<small>{hint}</small>}</label>}
export function Toast({text,type='ok'}:{text:string;type?:'ok'|'error'}){return <div className={`toast ${type}`}>{text}</div>}
export function fmtDate(value?:string,withTime=false){if(!value)return '未设置';const date=new Date(value);return new Intl.DateTimeFormat('zh-CN',withTime?{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}:{year:'numeric',month:'2-digit',day:'2-digit'}).format(date)}
export function dateInput(value?:string){if(!value)return '';const d=new Date(value);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16)}
