import { h } from 'vue'
import DefaultTheme from 'vitepress/theme'
import './style.css'

// The default theme plus a "Back to app" link. The app lives outside this
// site's /guide/ base, so it's a plain anchor — VitePress nav links would be
// rewritten under the base.
export default {
  extends: DefaultTheme,
  Layout: () =>
    h(DefaultTheme.Layout, null, {
      'nav-bar-content-after': () => h('a', { class: 'back-to-app', href: '/' }, '← Back to app'),
    }),
}
