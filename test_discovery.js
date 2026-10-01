const service={};const context={zcodeAgentService:service,zcodeTaskService:{}};const fiber={memoizedProps:{value:context}};global.document={querySelectorAll:()=>[{"__reactFiber$test":fiber}]};global.window={};const result=(()=>{const seen=new Set(),stack=[];
for(const el of document.querySelectorAll('*')){for(const k of Object.keys(el))if(k.startsWith('__reactContainer$')||k.startsWith('__reactFiber$'))stack.push(el[k]);if(stack.length)break;}
let count=0;while(stack.length&&count++<100000){const f=stack.pop();if(!f||seen.has(f))continue;seen.add(f);
const candidates=[f.memoizedProps?.value,f.memoizedProps?.services];
for(let d=f.dependencies?.firstContext;d;d=d.next)candidates.push(d.memoizedValue);
for(const c of candidates)if(c&&Object.prototype.hasOwnProperty.call(c,'zcodeAgentService')){window.__zcodeContinueService=c.zcodeAgentService;window.__zcodeContinueServices=c;return true;}
stack.push(f.child,f.sibling,f.return,f.current);}return false;})();if(!result||window.__zcodeContinueService!==service)throw Error("discovery failed");console.log("React service discovery fixture passed");