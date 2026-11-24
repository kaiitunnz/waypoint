export type Theme = "dark" | "light";

export const STORAGE_KEY = "waypoint-theme";
// Must match `--ground` in tokens.css.
export const THEME_COLORS: Record<Theme, string> = { dark: "#0b0e13", light: "#eeede9" };

// Runs inline in <head> before first paint so the wrong theme never flashes. It
// also creates the theme-color meta that theme.ts updates.
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
