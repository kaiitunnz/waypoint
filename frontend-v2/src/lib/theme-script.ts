export type Theme = "dark" | "light";

export const STORAGE_KEY = "waypoint-theme";
// The ground colour of each theme: the browser chrome and overscroll match it.
export const THEME_COLORS: Record<Theme, string> = { dark: "#0b0e13", light: "#eeede9" };

// Runs before first paint (inline in <head>), so there is no flash of the wrong
// theme. It also owns the one theme-color meta the provider keeps in sync.
export const ANTI_FLASH_SCRIPT = `(function(){
  var theme='dark';
  try{
    var t=localStorage.getItem('${STORAGE_KEY}');
    if(t==='light'||t==='dark'){theme=t;}
    else if(window.matchMedia('(prefers-color-scheme: light)').matches){theme='light';}
  }catch(e){}
  document.documentElement.dataset.theme=theme;
  var m=document.createElement('meta');
  m.name='theme-color';
  m.content=theme==='light'?'${THEME_COLORS.light}':'${THEME_COLORS.dark}';
  document.head.appendChild(m);
})();`;
