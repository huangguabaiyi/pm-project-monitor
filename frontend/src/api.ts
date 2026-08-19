const request = async <T>(path:string, init?:RequestInit):Promise<T> => {
  const response = await fetch(`/api${path}`, { headers:{'Content-Type':'application/json'}, ...init })
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`
    try { const body = await response.json(); detail = body.detail || detail } catch { /* noop */ }
    throw new Error(detail)
  }
  return response.json()
}
export const api = {
  get:<T>(p:string)=>request<T>(p),
  post:<T>(p:string, body:unknown)=>request<T>(p,{method:'POST',body:JSON.stringify(body)}),
  patch:<T>(p:string, body:unknown)=>request<T>(p,{method:'PATCH',body:JSON.stringify(body)}),
  del:<T>(p:string)=>request<T>(p,{method:'DELETE'}),
}
