import { KeyRound, Pencil, Plus, Trash2, UserRoundCheck } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { api } from '../api'
import { Field, Loading, PageHead, Toast } from '../components'
import type { Domain, Person } from '../types'

type PersonForm = { display_name:string; role_name:string; domain_id:string; feishu_open_id:string; email:string; description:string; active:boolean }
const blank:PersonForm = {display_name:'',role_name:'',domain_id:'',feishu_open_id:'',email:'',description:'',active:true}

export default function People(){
  const [items,setItems]=useState<Person[]>()
  const [domains,setDomains]=useState<Domain[]>([])
  const [editing,setEditing]=useState<Person|null>(null)
  const [form,setForm]=useState<PersonForm>(blank)
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const load=()=>Promise.all([api.get<Person[]>('/people'),api.get<Domain[]>('/domains')]).then(([people,domainItems])=>{setItems(people);setDomains(domainItems)})
  useEffect(()=>{void load()},[])
  if(!items)return <Loading/>

  function open(person?:Person){
    setEditing(person||({id:''} as Person))
    setForm(person?{
      display_name:person.display_name, role_name:person.role_name, domain_id:person.domain_id||'',
      feishu_open_id:person.feishu_open_id||'', email:person.email||'', description:person.description, active:person.active,
    }:blank)
  }
  async function save(e:FormEvent){
    e.preventDefault(); setError('')
    try{
      if(editing?.id)await api.patch(`/people/${editing.id}`,form)
      else await api.post('/people',form)
      setEditing(null);setMessage('人员配置已保存');load()
    }catch(err){setError((err as Error).message)}
  }
  async function deactivate(person:Person){
    if(!window.confirm(`停用人员「${person.display_name}」？历史需求不会被删除。`))return
    setError('')
    try{await api.del(`/people/${person.id}`);setMessage('人员已停用');await load()}
    catch(err){setError((err as Error).message)}
  }
  return <>
    <PageHead eyebrow="配置中心" title="人员配置" description="统一维护成员的交付领域、角色与飞书身份；需求和节点只选择这里已有的人员。" action={<button className="button primary" onClick={()=>open()}><Plus size={16}/>新增人员</button>}/>
    <div className="people-grid">{items.map(p=><article className={p.active?'person-card':'person-card inactive'} key={p.id}>
      <div className="avatar" style={p.domain?{background:`${p.domain.color}20`,color:p.domain.color}:undefined}>{p.display_name.slice(-2)}</div>
      <div><div className="person-title"><h3>{p.display_name}</h3>{p.domain&&<b style={{color:p.domain.color}}>{p.domain.name}</b>}</div><p>{p.role_name||'未设置角色'}</p><span className="open-id"><KeyRound size={11}/>{p.feishu_open_id||'未配置飞书 Open ID'}</span></div>
      <div className="person-actions"><button className="icon" onClick={()=>open(p)} aria-label="编辑"><Pencil size={16}/></button><button className="icon" onClick={()=>deactivate(p)} disabled={!p.active} aria-label="停用"><Trash2 size={16}/></button></div>
    </article>)}</div>
    {editing&&<div className="drawer-backdrop" onMouseDown={()=>setEditing(null)}><form className="drawer" onSubmit={save} onMouseDown={e=>e.stopPropagation()}>
      <div className="drawer-head"><div><UserRoundCheck/><span><strong>{editing.id?'编辑人员':'新增人员'}</strong><small>领域和 Open ID 会用于节点分工与飞书提醒</small></span></div><button type="button" className="icon" onClick={()=>setEditing(null)}>×</button></div>
      <Field label="姓名"><input required value={form.display_name} onChange={e=>setForm({...form,display_name:e.target.value})}/></Field>
      <Field label="所属交付领域"><select required value={form.domain_id} onChange={e=>setForm({...form,domain_id:e.target.value})}><option value="" disabled>选择交付领域</option>{domains.filter(d=>d.active).map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select></Field>
      <Field label="角色"><input value={form.role_name} onChange={e=>setForm({...form,role_name:e.target.value})} placeholder="例如：服务端研发"/></Field>
      <Field label="飞书 Open ID" hint="用于机器人消息中准确定位和提醒成员"><input value={form.feishu_open_id} onChange={e=>setForm({...form,feishu_open_id:e.target.value})} placeholder="例如 ou_xxxxxxxxx"/></Field>
      <Field label="邮箱"><input type="email" value={form.email} onChange={e=>setForm({...form,email:e.target.value})}/></Field>
      <Field label="说明"><textarea value={form.description} onChange={e=>setForm({...form,description:e.target.value})}/></Field>
      <label className="check"><input type="checkbox" checked={form.active} onChange={e=>setForm({...form,active:e.target.checked})}/> 可用于新的需求和节点</label>
      <div className="drawer-actions"><button type="button" className="button ghost" onClick={()=>setEditing(null)}>取消</button><button className="button primary">保存</button></div>
    </form></div>}
    {message&&<Toast text={message}/>} {error&&<Toast text={error} type="error"/>}
  </>
}
