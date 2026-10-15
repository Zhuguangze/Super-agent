import type { Directive } from 'vue'

type Handler = (ev: PointerEvent) => void

interface ClickOutsideEl extends HTMLElement {
  __clickOutsideOff?: () => void
}

function attach(el: ClickOutsideEl, handler: Handler): void {
  detach(el)
  // pointerdown 捕获阶段：先于 click 触发,点击另一个菜单的按钮时,
  // 本菜单先收起,随后的 click 再正常切换目标菜单
  const listener = (ev: PointerEvent): void => {
    const target = ev.target as Node | null
    if (target && el.contains(target)) return
    handler(ev)
  }
  document.addEventListener('pointerdown', listener, true)
  el.__clickOutsideOff = () => document.removeEventListener('pointerdown', listener, true)
}

function detach(el: ClickOutsideEl): void {
  el.__clickOutsideOff?.()
  delete el.__clickOutsideOff
}

/** v-click-outside——在元素外按下鼠标/触摸时触发回调（用于点击空白处收起弹层）。 */
export const vClickOutside: Directive<ClickOutsideEl, Handler> = {
  mounted(el, binding) {
    if (typeof binding.value === 'function') attach(el, binding.value)
  },
  updated(el, binding) {
    if (binding.value !== binding.oldValue && typeof binding.value === 'function') {
      attach(el, binding.value)
    }
  },
  unmounted(el) {
    detach(el)
  },
}
