import { Play,Radio,RefreshCw,Save,Sparkles,Tally4 } from 'lucide-react'
import { FormEvent,useEffect,useState } from 'react'
import { api } from '../api'
import { dateInput,Field,Loading,PageHead,Toast,fmtDate } from '../components'
import type { Job,Notification } from '../types'

export default function Automation(){
  const [jobs,setJobs]=useState<Job[]>()
  const [notifications,setNotifications]=useState<Notification[]>([])
  const [message,setMessage]=useState('')
  const load=()=>Promise.all([api.get<Job[]>('/jobs'),api.get<Notification[]>('/notifications?limit=8')]).then(([j,n])=>{setJobs(j);setNotifications(n)})
  useEffect(()=>{void load()},[])
  if(!jobs)return <Loading/>
  async function run(id:string){await api.post(`/jobs/${id}/run`,{});setMessage('任务已加入立即执行队列');load()}
  async function save(e:FormEvent<HTMLFormElement>,job:Job){
    e.preventDefault()
    const fd=new FormData(e.currentTarget)
    await api.patch(`/jobs/${job.id}`,{name:fd.get('name'),interval_seconds:Number(fd.get('interval_seconds')||job.interval_seconds),next_run_at:fd.get('next_run_at')||null,enabled:fd.get('enabled')==='on'})
    setMessage('定时器配置已保存')
    await load()
  }
  return <><PageHead eyebrow="运行中心" title="自动化" description="编辑风险扫描、通知投递和 AI 自动总结的定时运行配置。"/><div className="automation-grid">{jobs.map(j=><JobTimerCard job={j} key={j.id} onRun={run} onSave={save}/>)}</div><NotificationPanel items={notifications}/><section className="panel rule-explainer"><h2>风险规则</h2><p>系统不使用 buffer、环节默认耗时或复杂公式，只根据每个节点的预期时间、状态与依赖计算。</p><div className="rule-grid"><span>预期结束早于开始</span><span>超过预期结束仍未完成</span><span>前置节点晚于后置节点开始</span><span>后置已排期但前置未设置结束</span><span>后置提前启动而前置未完成</span><span>计划时间缺失或到期未启动</span></div></section>{message&&<Toast text={message}/>}</>
}

function JobTimerCard({job,onRun,onSave}:{job:Job;onRun:(id:string)=>void;onSave:(e:FormEvent<HTMLFormElement>,job:Job)=>void}){
  const label=job.job_type==='risk_scan'?'风险扫描':job.job_type==='ai_analysis'?'AI 自动总结':'通知投递'
  const Icon=job.job_type==='risk_scan'?Tally4:job.job_type==='ai_analysis'?Sparkles:Radio
  return <form className="panel job-card timer-card" onSubmit={e=>onSave(e,job)}><div className="job-icon"><Icon/></div><div><span>{label}</span><h3>{job.name}</h3><p>下次 {fmtDate(job.next_run_at,true)} · 上次 {job.last_status||'未执行'}</p></div><b className={job.enabled?'enabled':'disabled'}>{job.enabled?'运行中':'已停用'}</b><Field label="任务名称"><input name="name" defaultValue={job.name}/></Field><Field label="间隔秒数"><input name="interval_seconds" type="number" min={10} step={10} defaultValue={job.interval_seconds}/></Field><Field label="下次执行"><input name="next_run_at" type="datetime-local" defaultValue={dateInput(job.next_run_at)}/></Field><label className="check"><input name="enabled" type="checkbox" defaultChecked={job.enabled}/>启用定时器</label><div className="timer-actions"><button type="button" className="button ghost compact" onClick={()=>onRun(job.id)}><Play size={15}/>立即执行</button><button className="button primary compact"><Save size={15}/>保存</button></div></form>
}

function NotificationPanel({items}:{items:Notification[]}){
  return <section className="panel notification-panel"><div className="section-head"><div><h2>最近通知</h2><p>待投递、成功、失败和飞书返回错误会显示在这里。</p></div></div><div className="notification-list">{items.length?items.map(item=><div className="notification-row" key={item.id}><span><strong>{item.status}</strong><small>{item.id.slice(0,8)} · 尝试 {item.attempt_count} 次</small></span><span>{item.last_error||'无错误'}</span><span>{item.status==='pending'?`下次 ${fmtDate(item.available_at,true)}`:fmtDate(item.sent_at||item.created_at,true)}</span></div>):<div className="notification-empty">暂无通知记录。先运行风险扫描生成待投递通知。</div>}</div></section>
}
