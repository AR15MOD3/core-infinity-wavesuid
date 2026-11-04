// Standard-library DOM contract simulation, no browser or network side effects.
import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.textContent='';this.disabled=false;}
 append(...values){this.children.push(...values);}replaceChildren(...values){this.children=values;}
 setAttribute(){}addEventListener(name,fn){this.listeners[name]=fn;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
}
const ids=Object.fromEntries(['overview','status','refresh','count','accounts','message'].map(id=>[id,new Element(id)]));
const sent=[],parent={postMessage:value=>sent.push(value)};let listener;
const window={addEventListener:(_name,fn)=>{listener=fn;}};
const html=fs.readFileSync(new URL('../config.html',import.meta.url),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const context=vm.createContext({document:{getElementById:id=>ids[id],createElement:tag=>new Element(tag)},parent,window,setTimeout,clearTimeout,console});
vm.runInContext(script,context);
let checks=0;const check=fn=>{fn();checks++;};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const emit=data=>listener({source:parent,data});
const config={revision:'SYNTHETIC',pageActions:{accounts:{mutates:false},delete_account:{mutates:true}}};
check(()=>assert.equal(sent[0].type,'telyincore:config:get'));
listener({source:{},data:{type:'telyincore:config:state',config,active:true}});
check(()=>assert.equal(sent.length,1));
emit({type:'telyincore:config:state',config,active:false});
check(()=>assert.equal(sent.length,1,'hidden tab must not issue operator calls'));
emit({type:'telyincore:config:state',config,active:true});
check(()=>assert.equal(sent.at(-1).action,'accounts'));
function roster(enabled=true){return {bot:'ExampleBot',plugin:{enabled,loaded:enabled},people:1,accountCount:1,boundPeople:1,loginPeople:1,
 deleteAllowed:!enabled,accounts:[{id:'a'.repeat(64),revision:'b'.repeat(64),userId:'10001',platform:'dsh',uid:'123456789',bound:true,loginSaved:true,loginState:'已保存登录（未在线验证）'}]};}
emit({type:'telyincore:page:result',id:sent.at(-1).id,ok:true,result:roster()});await tick();
let button=ids.accounts.querySelectorAll('button')[0];
check(()=>assert.equal(button.disabled,true));
check(()=>assert.match(ids.count.textContent,/已保存 1 人/));
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
check(()=>assert.match(text(ids.accounts),/已保存库街区登录凭证/));
check(()=>assert.doesNotMatch(text(ids.accounts),/dsh|未在线验证/));
button.listeners.click();await tick();check(()=>assert.equal(sent.at(-1).action,'accounts'));
ids.refresh.listeners.click();
emit({type:'telyincore:page:result',id:sent.at(-1).id,ok:true,result:roster(false)});await tick();
button=ids.accounts.querySelectorAll('button')[0];check(()=>assert.equal(button.disabled,false));
button.listeners.click();
check(()=>assert.equal(sent.at(-1).action,'delete_account'));
check(()=>assert.equal(sent.at(-1).payload.active,'ExampleBot'));
check(()=>assert.equal(sent.at(-1).payload.revision,'b'.repeat(64)));
const count=sent.length;button.listeners.click();await tick();check(()=>assert.equal(sent.length,count,'no duplicate mutation'));
emit({type:'telyincore:page:result',id:sent.at(-1).id,ok:false,message:'SYNTHETIC cancelled'});await tick();
check(()=>assert.match(ids.message.textContent,/cancelled/));
check(()=>assert.equal(sent.length,count,'failed write is never retried'));
ids.overview.listeners.click();check(()=>assert.equal(sent.at(-1).type,'telyincore:page:overview'));
console.log(JSON.stringify({passed:checks,network:0,browserVisualVerified:false}));
