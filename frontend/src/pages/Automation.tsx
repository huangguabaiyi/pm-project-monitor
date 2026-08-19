import { DownloadCloud,Play,Radio,RefreshCw,Tally4,Trash2,UploadCloud } from 'lucide-react'
import { ChangeEvent,useEffect,useRef,useState } from 'react'
import { api } from '../api'
import { Field,Loading,PageHead,Toast,fmtDate } from '../components'
import type { DataMaintenanceResult,DeploymentUpdateStatus,Job,Notification } from '../types'

export default function Automation(){
  const [jobs,setJobs]=useState<Job[]>()
  const [notifications,setNotifications]=useState<Notification[]>([])
  const [message,setMessage]=useState('')
  const load=()=>Promise.all([api.get<Job[]>('/jobs'),api.get<Notification[]>('/notifications?limit=8')]).then(([j,n])=>{setJobs(j);setNotifications(n)})
  useEffect(()=>{void load()},[])
  if(!jobs)return <Loading/>
  async function run(id:string){await api.post(`/jobs/${id}/run`,{});setMessage('任务已加入立即执行队列');load()}
  return <><PageHead eyebrow="运行中心" title="自动化" description="定时重新计算节点计划风险，并将待发送通知可靠地投递到配置的渠道。"/><div className="automation-grid">{jobs.map(j=><article className="panel job-card" key={j.id}><div className="job-icon">{j.job_type==='risk_scan'?<Tally4/>:<Radio/>}</div><div><span>{j.job_type==='risk_scan'?'风险扫描':'通知投递'}</span><h3>{j.name}</h3><p>每 {j.interval_seconds>=3600?`${j.interval_seconds/3600} 小时`:`${j.interval_seconds} 秒`}运行 · 下次 {fmtDate(j.next_run_at,true)} · 上次 {j.last_status||'未执行'}</p></div><b className={j.enabled?'enabled':'disabled'}>{j.enabled?'运行中':'已停用'}</b><button className="button ghost compact" onClick={()=>run(j.id)}><Play size={15}/>立即执行</button></article>)}</div><NotificationPanel items={notifications}/><DeploymentUpdater/><DataMaintenance onChanged={load}/><section className="panel rule-explainer"><h2>风险规则</h2><p>系统不使用 buffer、环节默认耗时或复杂公式，只根据每个节点的预期时间、状态与依赖计算。</p><div className="rule-grid"><span>预期结束早于开始</span><span>超过预期结束仍未完成</span><span>前置节点晚于后置节点开始</span><span>后置已排期但前置未设置结束</span><span>后置提前启动而前置未完成</span><span>计划时间缺失或到期未启动</span></div></section>{message&&<Toast text={message}/>}</>
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

function DataMaintenance({onChanged}:{onChanged:()=>void}){
  const inputRef=useRef<HTMLInputElement>(null)
  const [preserveSettings,setPreserveSettings]=useState(true)
  const [busy,setBusy]=useState(false)
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const [lastResult,setLastResult]=useState<DataMaintenanceResult>()
  async function download(){
    setBusy(true);setError('')
    try{
      const response=await fetch('/api/admin/export')
      if(!response.ok)throw new Error(`导出失败 (${response.status})`)
      const blob=await response.blob()
      const disposition=response.headers.get('Content-Disposition')||''
      const match=/filename="([^"]+)"/.exec(disposition)
      const url=URL.createObjectURL(blob)
      const link=document.createElement('a')
      link.href=url
      link.download=match?.[1]||`pm-project-monitor-backup-${new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')}.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setMessage('备份文件已下载')
    }catch(err){setError((err as Error).message)}
    finally{setBusy(false)}
  }
  async function upload(event:ChangeEvent<HTMLInputElement>){
    const file=event.currentTarget.files?.[0]
    event.currentTarget.value=''
    if(!file)return
    if(!window.confirm(`导入「${file.name}」会先清空当前数据再恢复备份，继续？`))return
    setBusy(true);setError('')
    try{
      const backup=JSON.parse(await file.text())
      const result=await api.post<DataMaintenanceResult>('/admin/import',{backup,preserve_settings:preserveSettings})
      setLastResult(result);setMessage('备份已导入');onChanged()
    }catch(err){setError((err as Error).message)}
    finally{setBusy(false)}
  }
  async function clear(){
    if(!window.confirm(preserveSettings?'清空业务数据、模板、通知和编号？飞书/AI/定时任务配置会保留。':'清空全部数据和配置？这个操作不能撤销。'))return
    setBusy(true);setError('')
    try{
      const result=await api.post<DataMaintenanceResult>('/admin/clear',{preserve_settings:preserveSettings})
      setLastResult(result);setMessage('数据已清空');onChanged()
    }catch(err){setError((err as Error).message)}
    finally{setBusy(false)}
  }
  const resultText=lastResult?.imported?`已导入 ${Object.values(lastResult.imported).reduce((sum,value)=>sum+value,0)} 行`:lastResult?.deleted?`已删除 ${Object.values(lastResult.deleted).reduce((sum,value)=>sum+value,0)} 行`:''
  return <section className="panel data-maintenance"><div className="section-head"><div><h2>数据备份与迁移</h2><p>导出完整 JSON 备份，或从备份恢复到当前数据库。</p></div></div><div className="maintenance-body"><div className="maintenance-actions"><button className="button ghost" onClick={download} disabled={busy}><DownloadCloud size={16}/>导出数据</button><input ref={inputRef} type="file" accept="application/json,.json" hidden onChange={upload}/><button className="button primary" onClick={()=>inputRef.current?.click()} disabled={busy}><UploadCloud size={16}/>导入数据</button><button className="button danger" onClick={clear} disabled={busy}><Trash2 size={16}/>清空数据</button></div><label className="check"><input type="checkbox" checked={preserveSettings} onChange={e=>setPreserveSettings(e.currentTarget.checked)}/>导入或清空时保留飞书、AI 和定时任务配置</label><p className="maintenance-note">导出文件包含完整迁移数据，包括飞书 webhook 和 AI key，按敏感文件保管。</p>{busy&&<div className="loading deploy-running"><RefreshCw className="spin"/>正在处理数据…</div>}{resultText&&<div className="maintenance-result">{resultText}</div>}</div>{message&&<Toast text={message}/>} {error&&<Toast text={error} type="error"/>}</section>
}
