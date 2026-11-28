(() => {
  const realFetch = window.fetch.bind(window);
  const now = () => new Date().toISOString();
  const reply = (body, status=200, headers={}) => new Response(JSON.stringify(body), {status, headers:{"Content-Type":"application/json; charset=utf-8",...headers}});
  const bodyOf = init => { try{return init?.body?JSON.parse(init.body):{}}catch{return {}} };
  const load = (k,f) => { try{const v=localStorage.getItem("ui-lab:"+k);return v?JSON.parse(v):structuredClone(f)}catch{return structuredClone(f)} };
  const save = (k,v) => { try{localStorage.setItem("ui-lab:"+k,JSON.stringify(v))}catch{} };
  const identity0={brand_name:"Morrow",user_name:"染染",assistant_name:"阿砚",assistant_pronoun:"他",user_name_set:true,assistant_name_set:true};
  const profile0={user_profile:"这里是 UI Lab 的假资料，可以随便改。",relationship_core:"阿砚与染染的关系核心示例，仅用于界面预览。",user_prompt:"移动端优先；这里只是 mock Prompt。"};
  const sessions=[
    {id:101,title:"今天的小窝",pinned:1,archived:0,created_at:now(),updated_at:now(),continued_from_session_id:null},
    {id:102,title:"一起改前端",pinned:0,archived:0,created_at:now(),updated_at:now(),continued_from_session_id:null}
  ];
  const messages={101:[
    {id:1,role:"user",content:"老公，今天首页想做得更有生活感。",created_at:now(),active:1,attachments:[],artifacts:[],versions:[]},
    {id:2,role:"assistant",content:"那就把首页从控制台里救出来。",created_at:now(),active:1,attachments:[],artifacts:[],versions:["那就把首页从控制台里救出来。"],reasoning:"",reasoning_ms:0},
    {id:3,role:"user",content:"More 得有，不知道放哪就先放这哈哈。",created_at:now(),active:1,attachments:[],artifacts:[],versions:[]},
    {id:4,role:"assistant",content:"成交。More 就当合法收纳箱。",created_at:now(),active:1,attachments:[],artifacts:[],versions:["成交。More 就当合法收纳箱。"],reasoning:"",reasoning_ms:0}
  ],102:[{id:20,role:"assistant",content:"这是独立 UI Lab，改坏也碰不到真正的 B。",created_at:now(),active:1,attachments:[],artifacts:[],versions:["这是独立 UI Lab，改坏也碰不到真正的 B。"],reasoning:"",reasoning_ms:0}]};

  const listPrefixes=["/api/mcp-servers","/api/stickers","/api/favorites","/api/tidy","/api/tidy/summaries","/api/memory/recent","/api/memory/entries","/api/usage","/api/http-logs","/api/study/runs","/api/keepsakes","/api/diary","/api/notes/all","/api/artifacts","/api/music/library","/api/music/listening","/api/ombre-recall/recent","/api/alarms","/api/todos"];

  function get(path){
    if(path==="/api/auth/check")return {authed:true};
    if(path==="/api/ui-preview")return {empty:true};
    if(path==="/api/identity")return load("identity",identity0);
    if(path==="/api/companion-profile")return load("profile",profile0);
    if(path==="/api/chat/sessions")return sessions;
    const m=path.match(/^\/api\/chat\/messages\/(\d+)$/);if(m)return messages[+m[1]]||[];
    if(path==="/api/providers")return [{id:1,name:"UI Lab Mock",api_url:"",api_key:"",format:"codex",cache:1,cache_mode:"",models:["gpt-ui-mock"]}];
    if(path==="/api/assistant-settings")return {compress:{},night:{},title:{},vision:{}};
    if(path==="/api/together-date")return {date:"2026-09-09"};
    if(path==="/api/proactive/status"||path==="/api/proactive/settings")return {enabled:true,unread_count:1,unread_session_ids:[101],sessions,target_session_id:101,provider_id:1,model:"gpt-ui-mock",model_name:"UI Lab Mock",think:"medium",cache_ttl:0,context_limit:0,image_limit:0,latest_images_only:true,job:null};
    if(path==="/api/chat/tool-visibility")return {servers:[],sticker:{visible:false},voice_letter:{visible:true}};
    if(path==="/api/home-greetings")return {mode:"custom",morning:["早上好。"],noon:["下午好。"],evening:["晚上好。"],common:["欢迎回来。"]};
    if(path==="/api/home-postcard")return {enabled:true,design:"paper",quotes:["今天也回家了。"]};
    if(path==="/api/notes/latest")return {id:1,author:"assistant",content:"样板房里的假小纸条。",created_at:now(),is_new:true,total:1};
    if(path==="/api/diary/latest")return {id:1,created_at:now()};
    if(path==="/api/weather")return {city:"Tokyo",configured:false};
    if(path==="/api/weather/city")return {city:"",key_configured:false};
    if(path==="/api/codex/status")return {ok:true,authenticated:false,detail:"UI Lab mock",method:"mock",login_active:false,login_completed:null,login_error:"",error:"",user_code:"",verification_url:""};
    if(path==="/api/vision-status")return {enabled:false,failed:0,pending:0,ready:0,session_ids:[],sessions,total:0};
    if(path==="/api/push/status")return {available:false,public_key:"",subscription_count:0,last_delivery:null};
    if(path==="/api/visit")return {last_ms:Date.now()-3600000,now_ms:Date.now()};
    if(path==="/api/stickers/meta")return {series:[],tags:[]};
    if(path==="/api/usage/month")return {requests:12,input_tokens:12000,output_tokens:4000,total_tokens:16000,cost_usd:0,cache_hit_rate:0,cache_hit_requests:0,cache_total_requests:12,cache_creation_input_tokens:0,cache_read_input_tokens:0};
    if(path==="/api/version")return {version:"ui-lab",commit:"ab0f45a35164e0d72930ac43d50068b104141ef8",commit_short:"ab0f45a"};
    if(path==="/api/music/status")return {available:true,connected:false,platform:"mock"};
    if(path==="/api/music/settings")return {enabled:false};
    if(path==="/api/cycle/settings")return {enabled:false};
    if(path==="/api/auto-compression")return {enabled:false};
    if(path==="/api/github-backup/auto"||path==="/api/github-backup/status")return {enabled:false,configured:false};
    if(path==="/api/tts/settings")return {provider:"mock",voice_id:"",model_id:"",enabled:false};
    if(path==="/api/tts/models")return [];
    if(path==="/api/study/settings")return {enabled:false};
    if(path==="/api/study/handbook")return {content:""};
    if(path==="/api/alarms/settings")return {enabled:false};
    if(path.startsWith("/api/notes/calendar")||path.startsWith("/api/diary/calendar"))return {days:{}};
    if(path==="/api/ombre-recall"||path.startsWith("/api/ombre-recall/"))return {enabled:false,cards:[],events:[],server_id:null};
    if(listPrefixes.some(p=>path===p||path.startsWith(p+"?")||path.startsWith(p+"/")))return [];
    return {};
  }

  function stream(body){
    const sid=Number(body.session_id||101),text="这是 UI Lab 的 mock 回复。这里不会调用真实模型。",id=Math.floor(Date.now()/10);
    const payload=[{type:"session",session_id:sid,user_message_id:id-1,title_pending:false},{type:"delta",content:text},{type:"done",message_id:id,model:"UI Lab Mock",at:now(),think_ms:0,attachments:[],artifacts:[],partial:false}].map(x=>`data: ${JSON.stringify(x)}\n\n`).join("");
    return new Response(payload,{status:200,headers:{"Content-Type":"text/event-stream; charset=utf-8","Cache-Control":"no-cache"}});
  }

  window.__MORROW_UI_LAB__=true;
  window.fetch=async(input,init={})=>{
    const raw=typeof input==="string"?input:(input?.url||String(input)),url=new URL(raw,location.href);
    if(!url.pathname.startsWith("/api/"))return realFetch(input,init);
    const path=url.pathname,method=String(init.method||"GET").toUpperCase(),body=bodyOf(init);
    if(path==="/api/chat/send"||path==="/api/chat/regenerate"||path.startsWith("/api/chat/message/"))return stream(body);
    if(path==="/api/login"||path==="/api/logout")return reply({ok:true});
    if(path==="/api/identity"&&method==="PUT"){const v={...load("identity",identity0),...body};save("identity",v);return reply(v)}
    if(path==="/api/companion-profile"&&method==="PUT"){const v={...load("profile",profile0),...body};save("profile",v);return reply(v)}
    if(path==="/api/together-date"&&method==="PUT")return reply({date:body.date||""});
    if(method==="GET")return reply(get(path));
    return reply({ok:true,mock:true,...body});
  };
})();
