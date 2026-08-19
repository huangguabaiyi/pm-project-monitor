import { useEffect, useState } from 'react'
import { Bot, CheckCircle2, KeyRound, RefreshCw, Save, ShieldAlert, Sparkles } from 'lucide-react'
import { Field, Loading, PageHead, Toast } from '../components'
import { api } from '../api'
import type { AISettings as Settings, PlusLoginStatus } from '../types'

const models = ['gpt-5.4','gpt-5.3-codex','gpt-5.2','deepseek-chat','qwen-plus']

export default function AISettings(){
  const [settings,setSettings]=useState<Settings>()
  const [plus,setPlus]=useState<PlusLoginStatus>()
  const [apiKey,setApiKey]=useState('')
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const [saving,setSaving]=useState(false)
  const loadStatus=()=>api.get<PlusLoginStatus>('/ai-settings/plus/status').then(setPlus).catch(err=>setError((err as Error).message))
  useEffect(()=>{api.get<Settings>('/ai-settings').then(setSettings);void loadStatus()},[])
  useEffect(()=>{if(!plus?.running)return;const timer=window.setInterval(()=>void loadStatus(),1800);return()=>window.clearInterval(timer)},[plus?.running])
  if(!settings)return <Loading/>
  async function save(e:React.FormEvent){e.preventDefault();setSaving(true);setError('');const body:Record<string,unknown>={...settings};delete body.api_key;delete body.api_key_configured;delete body.updated_at;if(apiKey)body.api_key=apiKey;try{const result=await api.patch<Settings>('/ai-settings',body);setSettings(result);setApiKey('');setMessage('AI 配置已保存');}catch(err){setError((err as Error).message)}finally{setSaving(false)}}
  async function login(){setError('');try{const result=await api.post<PlusLoginStatus>('/ai-settings/plus/login',{});setPlus(result);setMessage('设备授权已启动，请按下方提示完成登录')}catch(err){setError((err as Error).message)}}
  return <><PageHead eyebrow="智能分析" title="AI 风险分析" description="综合节点备注、状态、时间、人员与依赖生成结构化风险结论；关闭后不影响规则风险。"/>
    <form className="ai-settings-grid" onSubmit={save}>
      <section className="panel ai-settings-main">
        <div className="section-head"><div><h2>分析服务</h2><p>AI 结论只会提高风险，不会覆盖或降低确定性的计划风险。</p></div><label className="switch"><input type="checkbox" checked={settings.enabled} onChange={e=>setSettings({...settings,enabled:e.target.checked})}/><i/><span>{settings.enabled?'AI 已开启':'AI 已关闭'}</span></label></div>
        <div className="provider-switch"><button type="button" className={settings.provider==='chatgpt_plus'?'active':''} onClick={()=>setSettings({...settings,provider:'chatgpt_plus'})}><Sparkles/>ChatGPT Plus <small>实验性</small></button><button type="button" className={settings.provider==='openai_compatible'?'active':''} onClick={()=>setSettings({...settings,provider:'openai_compatible'})}><KeyRound/>第三方 API <small>OpenAI 兼容</small></button></div>
        {settings.provider==='chatgpt_plus'?<div className="plus-login-box"><div className={plus?.authenticated?'login-state ok':'login-state'}>{plus?.authenticated?<CheckCircle2/>:<ShieldAlert/>}<div><strong>{plus?.authenticated?'ChatGPT 已登录':'需要设备授权'}</strong><p>{plus?.authenticated?'登录凭证已保存在 Docker 持久化卷中。':'首次启用需完成一次网页登录，容器升级不会丢失登录状态。'}</p></div></div><div className="inline-actions"><button type="button" className="button primary compact" onClick={login} disabled={plus?.running}>{plus?.running?<RefreshCw className="spin"/>:<Bot/>}{plus?.running?'等待授权':'登录 ChatGPT Plus'}</button><button type="button" className="button ghost compact" onClick={loadStatus}><RefreshCw/>刷新状态</button></div>{plus?.output&&<pre className="auth-output">{plus.output}</pre>}</div>:<div className="settings-form"><Field label="API Base URL" hint="例如 OpenAI、DeepSeek、通义等兼容地址"><input value={settings.base_url} onChange={e=>setSettings({...settings,base_url:e.target.value})}/></Field><Field label="API Key" hint={settings.api_key_configured?`已配置 ${settings.api_key}；留空保持不变`:'密钥保存后不会再次完整返回'}><input type="password" value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder={settings.api_key_configured?'输入新 Key 可替换':'输入 API Key'}/></Field></div>}
        <div className="settings-form"><Field label="模型" hint="可选择常用值，也可以直接填写服务商提供的模型名"><input list="ai-models" value={settings.model} onChange={e=>setSettings({...settings,model:e.target.value})}/><datalist id="ai-models">{models.map(model=><option key={model} value={model}/>)}</datalist></Field><Field label="默认分析 Prompt" hint="系统始终要求模型按照固定 JSON Schema 返回"><textarea rows={12} value={settings.prompt} onChange={e=>setSettings({...settings,prompt:e.target.value})}/></Field></div>
        <div className="ai-options"><label><input type="checkbox" checked={settings.auto_analyze} onChange={e=>setSettings({...settings,auto_analyze:e.target.checked})}/><span><strong>定时自动分析</strong><small>风险扫描时仅分析新增或信息发生变化的需求</small></span></label><label><input type="checkbox" checked={settings.include_in_feishu} onChange={e=>setSettings({...settings,include_in_feishu:e.target.checked})}/><span><strong>带入飞书机器人</strong><small>风险卡片中增加 AI 摘要与建议动作</small></span></label></div>
        <div className="form-actions"><button className="button primary" disabled={saving}><Save/>{saving?'保存中':'保存 AI 配置'}</button></div>
      </section>
      <aside className="panel ai-schema-card"><Sparkles/><h3>固定输出结构</h3><p>每次结果都包含风险等级、总结、置信度、交付预测、风险信号、建议动作和缺失信息。</p><code>risk_level</code><code>summary</code><code>confidence</code><code>delivery_forecast</code><code>signals[]</code><code>actions[]</code><code>missing_information[]</code><small>Plus 属于实验通道；登录或额度失效只会暂停 AI，不影响基础功能。</small></aside>
    </form>{message&&<Toast text={message}/>} {error&&<Toast text={error} type="error"/>}</>
}
