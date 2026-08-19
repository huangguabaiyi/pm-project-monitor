import { Activity, BellRing, GitBranch, LayoutDashboard, Menu, Plus, Send, Settings, Sparkles, Users, X } from 'lucide-react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useState } from 'react'
import Overview from './pages/Overview'
import Requirements from './pages/Requirements'
import RequirementCreate from './pages/RequirementCreate'
import RequirementDetail from './pages/RequirementDetail'
import People from './pages/People'
import WorkflowConfig from './pages/WorkflowConfig'
import Automation from './pages/Automation'
import WebhookSettings from './pages/WebhookSettings'
import AISettings from './pages/AISettings'
import SystemMaintenance from './pages/SystemMaintenance'

const nav = [
  ['/', '总览', LayoutDashboard],
  ['/requirements', '需求', Activity],
  ['/people', '人员配置', Users],
  ['/workflow', '交付配置', GitBranch],
  ['/webhook', 'Webhook 配置', Send],
  ['/ai-settings', 'AI 分析', Sparkles],
  ['/automation', '自动化', BellRing],
  ['/maintenance', '系统维护', Settings],
] as const

export default function App(){
  const [open,setOpen]=useState(false)
  return <div className="app-shell">
    <aside className={open?'sidebar open':'sidebar'}>
      <div className="brand"><span className="brand-mark">P</span><div><strong>Pulse</strong><small>需求交付管理</small></div><button className="icon mobile" onClick={()=>setOpen(false)}><X size={18}/></button></div>
      <nav>{nav.map(([to,label,Icon])=><NavLink key={to} to={to} end={to==='/'} onClick={()=>setOpen(false)}><Icon size={18}/><span>{label}</span></NavLink>)}</nav>
      <div className="sidebar-foot"><div className="live-dot"/>节点计划驱动风险</div>
    </aside>
    <main>
      <header className="topbar"><button className="icon mobile" onClick={()=>setOpen(true)}><Menu/></button><div className="topbar-title">让每个交付节点清晰可见</div><NavLink className="button primary compact" to="/requirements/new"><Plus size={16}/>新建需求</NavLink></header>
      <div className="page-wrap"><Routes>
        <Route path="/" element={<Overview/>}/>
        <Route path="/requirements" element={<Requirements/>}/>
        <Route path="/requirements/new" element={<RequirementCreate/>}/>
        <Route path="/requirements/:id" element={<RequirementDetail/>}/>
        <Route path="/people" element={<People/>}/>
        <Route path="/workflow" element={<WorkflowConfig/>}/>
        <Route path="/webhook" element={<WebhookSettings/>}/>
        <Route path="/ai-settings" element={<AISettings/>}/>
        <Route path="/automation" element={<Automation/>}/>
        <Route path="/maintenance" element={<SystemMaintenance/>}/>
        <Route path="*" element={<Navigate to="/"/>}/>
      </Routes></div>
    </main>
  </div>
}
