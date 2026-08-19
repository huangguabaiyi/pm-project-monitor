import { CheckCircle2, EyeOff, Save, Send, ShieldCheck } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { api } from '../api'
import { Field, Loading, PageHead, Toast } from '../components'
import type { WebhookSettings as Settings } from '../types'

export default function WebhookSettings(){
  const [settings,setSettings]=useState<Settings>()
  const [testUrl,setTestUrl]=useState('')
  const [prodUrl,setProdUrl]=useState('')
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  useEffect(()=>{api.get<Settings>('/webhook-settings').then(setSettings)},[])
  if(!settings)return <Loading/>
  async function save(e:FormEvent){
    e.preventDefault();setError('')
    const body:Record<string,unknown>={enabled:settings!.enabled,runtime_environment:settings!.runtime_environment,bot_keyword:settings!.bot_keyword}
    if(testUrl.trim())body.test_webhook_url=testUrl.trim()
    if(prodUrl.trim())body.prod_webhook_url=prodUrl.trim()
    try{const result=await api.patch<Settings>('/webhook-settings',body);setSettings(result);setTestUrl('');setProdUrl('');setMessage('Webhook 配置已安全保存')}
    catch(err){setError((err as Error).message)}
  }
  async function clear(environment:'test'|'prod'){
    const result=await api.patch<Settings>('/webhook-settings',{[`${environment}_webhook_url`]:null})
    setSettings(result);setMessage(`${environment==='test'?'测试':'正式'} Webhook 已清除`)
  }
  return <>
    <PageHead eyebrow="通知中心" title="Webhook 配置" description="独立管理飞书机器人通知渠道；地址保存后只显示脱敏尾号，不会再次返回完整密钥。"/>
    <form className="webhook-layout" onSubmit={save}>
      <section className="panel webhook-main"><div className="section-head"><div><h2>飞书机器人</h2><p>仅接受飞书或 Lark 官方自定义机器人 Webhook 地址</p></div><label className="switch"><input type="checkbox" checked={settings.enabled} onChange={e=>setSettings({...settings,enabled:e.target.checked})}/><i/><span>{settings.enabled?'通知已启用':'通知已停用'}</span></label></div>
        <div className="webhook-fields">
          <Field label="当前运行环境"><div className="environment-switch"><button type="button" className={settings.runtime_environment==='test'?'active':''} onClick={()=>setSettings({...settings,runtime_environment:'test'})}>测试环境</button><button type="button" className={settings.runtime_environment==='prod'?'active':''} onClick={()=>setSettings({...settings,runtime_environment:'prod'})}>正式环境</button></div></Field>
          <Field label="机器人安全关键词" hint="如果机器人开启关键词校验，系统会自动注入该关键词"><input value={settings.bot_keyword} onChange={e=>setSettings({...settings,bot_keyword:e.target.value})} placeholder="需求交付提醒"/></Field>
          <WebhookField label="测试环境 Webhook" configured={settings.test_configured} masked={settings.test_webhook_url} value={testUrl} setValue={setTestUrl} onClear={()=>clear('test')}/>
          <WebhookField label="正式环境 Webhook" configured={settings.prod_configured} masked={settings.prod_webhook_url} value={prodUrl} setValue={setProdUrl} onClear={()=>clear('prod')}/>
        </div>
        <div className="form-actions"><button className="button primary"><Save size={16}/>保存配置</button></div>
      </section>
      <aside className="webhook-side"><div className="panel security-card"><ShieldCheck/><h3>密钥保护</h3><p>管理接口只返回脱敏值。更新地址时输入完整 Webhook；留空则保留原配置。</p></div><div className="panel delivery-card"><Send/><div><span>当前投递通道</span><strong>{settings.runtime_environment==='test'?'测试环境':'正式环境'}</strong><small>{settings.runtime_environment==='test'?(settings.test_configured?'已配置':'尚未配置'):(settings.prod_configured?'已配置':'尚未配置')}</small></div></div></aside>
    </form>
    {message&&<Toast text={message}/>} {error&&<Toast text={error} type="error"/>}
  </>
}

function WebhookField({label,configured,masked,value,setValue,onClear}:{label:string;configured:boolean;masked?:string;value:string;setValue:(v:string)=>void;onClear:()=>void}){
  return <div className="webhook-field"><div className="webhook-label"><span>{label}</span>{configured?<b><CheckCircle2/>已配置 {masked}</b>:<em>未配置</em>}</div><div className="secret-input"><EyeOff/><input type="password" value={value} onChange={e=>setValue(e.target.value)} placeholder={configured?'输入新地址可替换当前配置':'粘贴完整 Webhook 地址'}/>{configured&&<button type="button" onClick={onClear}>清除</button>}</div></div>
}
