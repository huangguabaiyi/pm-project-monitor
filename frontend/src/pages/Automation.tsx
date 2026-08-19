import { DownloadCloud,Play,Radio,RefreshCw,Tally4 } from 'lucide-react'
import { useEffect,useState } from 'react'
import { api } from '../api'
import { Field,Loading,PageHead,Toast,fmtDate } from '../components'
import type { DeploymentUpdateStatus,Job,Notification } from '../types'

export default function Automation(){
  const [jobs,setJobs]=useState<Job[]>()
  const [notifications,setNotifications]=useState<Notification[]>([])
  const [message,setMessage]=useState('')
  const load=()=>Promise.all([api.get<Job[]>('/jobs'),api.get<Notification[]>('/notifications?limit=8')]).then(([j,n])=>{setJobs(j);setNotifications(n)})
  useEffect(()=>{void load()},[])
  if(!jobs)return <Loading/>
  async function run(id:string){await api.post(`/jobs/${id}/run`,{});setMessage('任务已加入立即执行队列');load()}
  return <><PageHead eyebrow="运行中心" title="自动化" description="定时重新计算节点计划风险，并将待发送通知可靠地投递到配置的渠道。"/><div className="automation-grid">{jobs.map(j=><article className="panel job-card" key={j.id}><div className="job-icon">{j.job_type==='risk_scan'?<Tally4/>:<Radio/>}</div><div><span>{j.job_type==='risk_scan'?'风险扫描':'通知投递'}</span><h3>{j.name}</h3><p>每 {j.interval_seconds>=3600?`${j.interval_seconds/3600} 小时`:`${j.interval_seconds} 秒`}运行 · 下次 {fmtDate(j.next_run_at,true)} · 上次 {j.last_status||'未执行'}</p></div><b className={j.enabled?'enabled':'disabled'}>{j.enabled?'运行中':'已停用'}</b><button className="button ghost compact" onClick={()=>run(j.id)}><Play size={15}/>立即执行</button></article>)}</div><NotificationPanel items={notifications}/><DeploymentUpdater/><section className="panel rule-explainer"><h2>风险规则</h2><p>系统不使用 buffer、环节默认耗时或复杂公式，只根据每个节点的预期时间、状态与依赖计算。</p><div className="rule-grid"><span>预期结束早于开始</span><span>超过预期结束仍未完成</span><span>前置节点晚于后置节点开始</span><span>后置已排期但前置未设置结束</span><span>后置提前启动而前置未完成</span><span>计划时间缺失或到期未启动</span></div></section>{message&&<Toast text={message}/>}</>
}

function NotificationPanel({items}:{items:Notification[]}){
  return <section className="panel notification-panel"><div className="section-head"><div><h2>最近通知</h2><p>待投递、成功、失败和飞书返回错误会显示在这里。</p></div></div><div className="notification-list">{items.length?items.map(item=><div className="notification-row" key={item.id}><span><strong>{item.status}</strong><small>{item.id.slice(0,8)} · 尝试 {item.attempt_count} 次</small></span><span>{item.last_error||'无错误'}</span><span>{item.status==='pending'?`下次 ${fmtDate(item.available_at,true)}`:fmtDate(item.sent_at||item.created_at,true)}</span></div>):<div className="notification-empty">暂无通知记录。先运行风险扫描生成待投递通知。</div>}</div></section>
}

function DeploymentUpdater(){
  const [status,setStatus]=useState<DeploymentUpdateStatus>()
  const [skipBackup,setSkipBackup]=useState(false)
  const [busy,setBusy]=useState(false)
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const load=()=>api.get<DeploymentUpdateStatus>('/deployment/update-status').then(setStatus)
  useEffect(()=>{void load()},[])
  useEffect(()=>{if(!status?.running)return;const id=window.setInterval(()=>{void load()},3000);return()=>window.clearInterval(id)},[status?.running])
  async function check(){
    setBusy(true);setError('')
    try{const result=await api.post<DeploymentUpdateStatus>('/deployment/check-updates',{});setStatus(result);setMessage('GitHub 更新检查完成')}
    catch(err){setError((err as Error).message)}
    finally{setBusy(false)}
  }
  async function apply(){
    setBusy(true);setError('')
    try{const result=await api.post<DeploymentUpdateStatus>('/deployment/apply-update',{skip_backup:skipBackup});setStatus(result);setMessage('部署更新已开始，页面会自动刷新状态')}
    catch(err){setError((err as Error).message)}
    finally{setBusy(false)}
  }
  if(!status)return <section className="panel deploy-card"><Loading/></section>
  const canRun=status.enabled&&status.script_exists&&!status.running&&!busy
  return <section className="panel deploy-card"><div className="section-head"><div><h2>部署更新</h2><p>从当前服务器仓库检查 GitHub 更新，并执行 Docker Compose 重建。</p></div><span className={status.enabled?'deploy-state enabled':'deploy-state disabled'}>{status.enabled?'已启用':'未启用'}</span></div><div className="deploy-body"><div className="deploy-meta"><span><strong>分支</strong>{status.branch||'未识别'}</span><span><strong>仓库</strong>{status.repo_path}</span><span><strong>脚本</strong>{status.script_exists?'已安装':'未找到'}</span><span><strong>上次结果</strong>{status.last_exit_code===undefined||status.last_exit_code===null?'未执行':status.last_exit_code===0?'成功':'失败'}</span></div>{!status.enabled&&<div className="form-warning">如需从页面触发部署，请在服务器 API 环境变量中设置 REQUIREMENT_MONITOR_DEPLOY_UPDATE_ENABLED=true，然后重启服务。</div>}<div className="deploy-actions"><button className="button ghost" onClick={check} disabled={!canRun}><RefreshCw size={16}/>检查 GitHub 更新</button><button className="button primary" onClick={apply} disabled={!canRun}><DownloadCloud size={16}/>{status.running?'更新中…':'执行更新'}</button><Field label="更新选项"><label className="check"><input type="checkbox" checked={skipBackup} onChange={e=>setSkipBackup(e.currentTarget.checked)}/>跳过数据库备份</label></Field></div>{status.running&&<div className="loading deploy-running"><RefreshCw className="spin"/>部署更新正在执行…</div>}{status.last_output&&<pre className="deploy-output">{status.last_output}</pre>}</div>{message&&<Toast text={message}/>} {error&&<Toast text={error} type="error"/>}</section>
}
