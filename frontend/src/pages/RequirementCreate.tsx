import { ArrowLeft, ExternalLink, GitBranch } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Field, PageHead, Toast } from '../components'
import type { Person, Requirement, RequirementLifecycleStatus, Template } from '../types'

export default function RequirementCreate(){
  const nav=useNavigate()
  const [people,setPeople]=useState<Person[]>([])
  const [templates,setTemplates]=useState<Template[]>([])
  const [error,setError]=useState('')
  const [saving,setSaving]=useState(false)
  const [lifecycleStatus,setLifecycleStatus]=useState<RequirementLifecycleStatus>('active')
  useEffect(()=>{Promise.all([api.get<Person[]>('/people'),api.get<Template[]>('/templates')]).then(([p,t])=>{setPeople(p.filter(x=>x.active));setTemplates(t.filter(x=>x.active&&x.node_count>0))})},[])
  async function submit(e:FormEvent<HTMLFormElement>){
    e.preventDefault();setSaving(true);setError('')
    const fd=new FormData(e.currentTarget)
    const body=Object.fromEntries(fd)
    for(const key of ['meego_url','requirement_url','figma_url'])if(!body[key])delete body[key]
    try{const result=await api.post<Requirement>('/requirements',body);nav(`/requirements/${result.id}`)}catch(err){setError((err as Error).message);setSaving(false)}
  }
  return <>
    <PageHead eyebrow="新建需求" title="创建需求交付实例" description="这里只选择已经配置好的人员和模板，不在需求里临时创建配置。" action={<Link className="button ghost" to="/requirements"><ArrowLeft size={16}/>返回列表</Link>}/>
    <form className="form-panel panel" onSubmit={submit}>
      <div className="form-section"><div className="section-icon"><GitBranch/></div><div><h2>基本信息</h2><p>创建后会复制模板节点，后续模板修改不会影响已经开始的需求。</p></div></div>
      <div className="form-grid">
        <Field label="需求名称"><input name="name" required placeholder="一句话描述需求"/></Field>
        <Field label="需求状态"><div className="requirement-state-picker"><button type="button" className={lifecycleStatus==='active'?'active':''} onClick={()=>setLifecycleStatus('active')}><strong>进行中</strong><small>参与风险、通知与 AI 总结</small></button><button type="button" className={lifecycleStatus==='planned'?'active':''} onClick={()=>setLifecycleStatus('planned')}><strong>计划中</strong><small>仅维护节点计划，不参与自动处理</small></button></div><input type="hidden" name="lifecycle_status" value={lifecycleStatus}/></Field>
        <Field label="需求负责人"><select name="owner_id" required defaultValue=""><option value="" disabled>选择已配置人员</option>{people.map(p=><option value={p.id} key={p.id}>{p.display_name} · {p.role_name}</option>)}</select></Field>
        <Field label="交付模板"><select name="template_id" required defaultValue=""><option value="" disabled>选择节点模板</option>{templates.map(t=><option value={t.id} key={t.id}>{t.name} · {t.node_count} 个节点</option>)}</select></Field>
        <Field label="目标版本"><input name="target_version" placeholder="可选，例如 v4.9.0"/></Field>
        <Field label="说明"><textarea name="notes" rows={3} placeholder="可选，补充交付背景"/></Field>
      </div>
      <div className="form-subsection"><ExternalLink/><div><h3>关联资料</h3><p>以下链接均为非必填，创建后会展示在需求详情。</p></div></div>
      <div className="form-grid link-fields">
        <Field label="Meego 地址"><input name="meego_url" type="url" placeholder="https://project.meego.cn/…"/></Field>
        <Field label="需求链接地址"><input name="requirement_url" type="url" placeholder="https://…"/></Field>
        <Field label="Figma 地址"><input name="figma_url" type="url" placeholder="https://www.figma.com/…"/></Field>
      </div>
      {(!people.length||!templates.length)&&<div className="form-warning">需要先在“人员配置”和“交付配置”中准备可用数据。</div>}
      <div className="form-actions"><Link className="button ghost" to="/requirements">取消</Link><button className="button primary" disabled={saving||!people.length||!templates.length}>{saving?'正在创建…':'创建并进入节点计划'}</button></div>
    </form>{error&&<Toast text={error} type="error"/>}
  </>
}
