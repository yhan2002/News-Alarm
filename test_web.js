// Offline refresh checks: node test_web.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
const node = (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, {value:'', open:false, handlers:{}, attributes:{}, addEventListener(name,fn){this.handlers[name]=fn;}, setAttribute(name,value){this.attributes[name]=value;}, focus(){this.focused=true;}});
  return nodes.get(selector);
};
const context = vm.createContext({
  AbortController,
  localStorage:{getItem(){return "light";},setItem(key,value){this.saved=value;}},
  document:{body:{dataset:{}},querySelectorAll(){return [];},documentElement:{dataset:{}},hidden:false, querySelector:node, addEventListener(){}},
  window:{addEventListener(){}},
  fetch:() => new Promise(() => {}), // Keep initial request in flight.
  setInterval(callback, ms) { assert.equal(ms, 60_000); this.tick = callback; },
});
vm.runInContext(fs.readFileSync(__dirname + '/web/app.js', 'utf8'), context);
vm.runInContext("activeView='radar';",context);
vm.runInContext('globalThis.calls = 0; load = () => { globalThis.calls++; };', context);
node('#mode').value = 'saved';
vm.runInContext('refreshSaved()', context);
assert.equal(context.calls, 0, 'Do not overlap an in-flight request');
vm.runInContext('requestController = undefined; refreshSaved()', context);
assert.equal(context.calls, 1);
context.document.hidden = true;
vm.runInContext('refreshSaved()', context);
assert.equal(context.calls, 1, 'Hidden tabs must not poll');
context.document.hidden = false;
node('#mode').value = 'demo';
vm.runInContext('refreshSaved()', context);
assert.equal(context.calls, 1, 'Demo must not poll');
node('#mode').value = 'saved';
node('#setup-dialog').open = true;
vm.runInContext('refreshSaved()', context);
assert.equal(context.calls, 1, 'Do not disrupt the collection dialog');
console.log('Saved dashboard refresh checks passed.');
// New terminal: exact stock focus, safe rendering, all archive pages remain reachable.
node('#issue-search').value = 'MS'; node('#issue-level').value = 'all'; node('#issue-category').value = 'all';
vm.runInContext(`data={issues:[{id:'one',score:80,in_scope:true,title:'MS earnings',category:'실적',links:[{symbol:'MS',name:'Morgan Stanley'}]},
{id:'two',score:80,in_scope:true,title:'MSFT earnings',category:'실적',links:[{symbol:'MSFT',name:'Microsoft'}]}]}; issueStock='MS';`, context);
assert.equal(vm.runInContext('visibleIssues().length', context), 1);
context.URL = URL;
assert.equal(vm.runInContext(`safeURL('javascript:alert(1)')`, context), '');
assert.equal(vm.runInContext(`esc('<script>')`, context), '&lt;script&gt;');
for(const id of ['macro-filter','importance-filter','sentiment-filter'])node('#'+id).value='all';
node('#search').value='';
vm.runInContext(`data={issues:[],articles:Array.from({length:31},(_,n)=>({id:String(n),title:'Article '+n,summary:'',source:'test',
url:'https://example.com/'+n,published_at:'2026-09-10T00:00:00Z',links:[],sentiment:{label:'neutral',score:0,engine:'test'},importance:{score:0,reasons:[]}}))}; renderFeed();`,context);
assert.equal((node('#articles').innerHTML.match(/class="article"/g)||[]).length,30);
vm.runInContext('archivePage=1; renderFeed();', context);
assert.equal((node('#articles').innerHTML.match(/class="article"/g)||[]).length,1);
console.log('Terminal stock focus, safe HTML and archive pagination checks passed.');
vm.runInContext(`data={articles:[{id:'deal',title:'Deal source',url:'https://example.com/deal',published_at:'2026-09-10T00:00:00Z',sentiment:{label:'neutral'},summary:''}],issues:[{id:'deal',title:'Deal',reasons:['인수·합병'],category:'인수·합병',level:'본문 평가 대기',basis:'제목 규칙',article_ids:['deal'],sources:['example'],sentiments:{neutral:1},links:[{symbol:'CEG',name:'Constellation',evidence:['CEG'],article_ids:['deal']}],themes:[{name:'전기차·에너지',evidence:['energy'],article_ids:['deal']}],transaction:{buyer:'Constellation',asset:'RISEC',seller:'Shell <unsafe>',amount:'$715M',article_id:'deal'}}]};selectedIssue='deal';graphMode='structure';renderFocus();`,context);
assert.ok(node('#surface').innerHTML.includes('인수자'));
assert.ok(node('#surface').innerHTML.includes('Shell &lt;unsafe&gt;'));
assert.ok(node('#surface').innerHTML.includes('$715M'));
assert.ok(node('#keyword-links').innerHTML.includes('사업 관계 미확인'));
assert.ok(node('#surface').innerHTML.includes('<svg'));
assert.ok(node('#surface').innerHTML.includes('매각'));
assert.ok(node('#surface').innerHTML.includes('marker-end'));
vm.runInContext(`selectedEdge='stock:CEG';renderEvidence();`,context);
assert.ok(node('#edge-evidence').innerHTML.includes('https://example.com/deal'));
console.log('Explicit transaction roles, escaped names and evidence links passed.');

vm.runInContext(`data.issues[0].last_at='2026-09-10T00:00:00Z';
data.articles.push({id:'other-source',title:'CEG reports earnings',url:'https://example.com/earnings'});
data.issues.push({id:'other',title:'CEG reports earnings',category:'실적',article_ids:['other-source'],last_at:'2026-09-10T01:00:00Z',links:[{symbol:'CEG',article_ids:['other-source'],evidence:['CEG']}]},
{id:'unrelated',title:'Unrelated',category:'실적',article_ids:['unrelated'],last_at:'2026-09-10T02:00:00Z',links:[{symbol:'AAPL',article_ids:['unrelated'],evidence:['AAPL']}]},
{id:'overlap',title:'Duplicate article',category:'실적',article_ids:['deal'],last_at:'2026-09-10T02:00:00Z',links:[{symbol:'CEG',article_ids:['deal'],evidence:['CEG']}]});
graphMode='related';renderNetwork(data.issues[0]);`,context);
assert.equal(vm.runInContext('relatedIssues(data.issues[0]).length',context),1,'Only different articles sharing a direct company connect');
assert.ok(node('#surface').innerHTML.includes('data-related="other"'));
assert.ok(!node('#surface').innerHTML.includes('Unrelated'));
assert.ok(node('#surface').innerHTML.includes('shared:other:CEG'));
assert.ok(node('#surface').innerHTML.includes('공통 기업'));
vm.runInContext(`selectedEdge='shared:other:CEG';renderEvidence();`,context);
assert.ok(node('#edge-evidence').innerHTML.includes('https://example.com/deal'));
assert.ok(node('#edge-evidence').innerHTML.includes('https://example.com/earnings'));
vm.runInContext(`data.issues[0].transaction=null;graphMode='structure';renderNetwork(data.issues[0]);`,context);
assert.ok(!node('#surface').innerHTML.includes('marker-end'));
assert.ok(node('#surface').innerHTML.includes('직접 언급'));
assert.ok(!node('#surface').innerHTML.includes('NaN'));
vm.runInContext(`selectedIssue='missing';renderFocus();`,context);
assert.equal(node('#map-tabs').innerHTML,'');
assert.equal(node('#keyword-links').innerHTML,'');
console.log('Shared-company graph, evidence from both stories, explicit direction and empty-state checks passed.');

const panel=node('.map-panel'),modal=node('#map-dialog'),expand=node('#expand-map');
const originalParent={insertBefore(item,next){assert.equal(item,panel);assert.equal(next,'next-panel');panel.parentNode=this;}};
panel.parentNode=originalParent;panel.nextSibling='next-panel';
modal.append=item=>{assert.equal(item,panel);panel.parentNode=modal;};
modal.showModal=()=>{modal.open=true;};
modal.close=()=>{modal.open=false;modal.handlers.close();};
expand.handlers.click();
assert.equal(modal.open,true);assert.equal(panel.parentNode,modal);assert.equal(expand.attributes['aria-expanded'],'true');
expand.handlers.click();
assert.equal(modal.open,false);assert.equal(panel.parentNode,originalParent);assert.equal(expand.attributes['aria-expanded'],'false');
expand.handlers.click();modal.close(); // Native Escape produces the same dialog close event.
assert.equal(panel.parentNode,originalParent);assert.equal(expand.focused,true);
assert.ok(fs.readFileSync(__dirname+'/web/style.css','utf8').includes('.map-dialog .network-svg'));
console.log('Map expand/restore, native dialog dismissal and focus restoration passed.');

assert.equal(context.document.documentElement.dataset.theme,'light','Restore saved theme on page load');
node('#theme-toggle').handlers.click();
assert.equal(context.document.documentElement.dataset.theme,'dark');
assert.equal(context.localStorage.saved,'dark');
node('#theme-toggle').handlers.click();
assert.equal(context.document.documentElement.dataset.theme,'light');
assert.equal(node('#theme-toggle').attributes['aria-label'],'다크 모드로 전환');
context.localStorage.getItem=()=>{throw Error('storage blocked');};
context.localStorage.setItem=()=>{throw Error('storage blocked');};
vm.runInContext('restoreTheme();applyTheme("invalid");',context);
assert.equal(context.document.documentElement.dataset.theme,'dark','Unavailable/corrupt storage falls back safely');
node('#theme-toggle').handlers.click();
assert.equal(context.document.documentElement.dataset.theme,'light','Toggle works without storage permission');
console.log('Light/dark persistence, labels and blocked-storage fallback passed.');

node('#issue-search').value='';node('#issue-category').value='all';node('#issue-level').value='all';node('#issue-sort').value='priority';
vm.runInContext(`issueStock=''; const base=data.issues[0]; data.issues=[{...base,id:'high',title:'High <unsafe>',score:90,last_at:'2026-09-10T00:00:00Z'}, {...base,id:'new',title:'New story',score:70,last_at:'2026-09-11T00:00:00Z'}]; selectedIssue=''; renderIssues();`,context);
assert.equal(vm.runInContext('selectedIssue',context),'high');
assert.ok(node('#map-title').innerHTML.includes('자동 선택'));
assert.ok(node('#map-title').innerHTML.includes('High &lt;unsafe&gt;'));
assert.ok(node('#issues').innerHTML.includes('선택한 사건'));
node('#issue-sort').value='latest';node('#issue-sort').handlers.change();
assert.equal(vm.runInContext('visibleIssues()[0].id',context),'new');
assert.equal(vm.runInContext('selectedIssue',context),'high','Sorting keeps the story being read');
vm.runInContext(`handleTarget({dataset:{issue:'new'}});renderIssues();`,context);
assert.ok(node('#map-title').innerHTML.includes('목록에서 직접 선택'));
assert.equal(vm.runInContext('selectedIssue',context),'new','Refresh keeps manual selection');
node('#issue-search').value='High';node('#issue-search').handlers.input();
assert.equal(vm.runInContext('selectedIssue',context),'high');
assert.ok(node('#map-title').innerHTML.includes('자동 선택'));
console.log('Explicit ordering, selection origin, escaping and selection persistence passed.');

vm.runInContext(`const anchor=data.issues[0]; data.issues=[anchor,...Array.from({length:12},(_,n)=>({...anchor,id:'related-'+n,article_ids:['unique-'+n],title:'Long <unsafe> '+ 'W'.repeat(180),score:n,last_at:'2026-09-11T00:00:00Z',links:anchor.links.map(l=>({...l,article_ids:['unique-'+n]}))}))];graphMode='related';renderNetwork(anchor);`,context);
assert.equal(vm.runInContext('relatedIssues(data.issues[0]).length',context),12);
assert.equal(vm.runInContext('relatedIssues(data.issues[0])[0].issue.score',context),11);
assert.equal((node('#surface').innerHTML.match(/class="related-story"/g)||[]).length,12);
assert.ok(node('#surface').innerHTML.includes('related-0'));
assert.ok(node('#surface').innerHTML.includes('&lt;unsafe&gt;'));
const card=vm.runInContext(`graphNode({x:0,y:0,w:175,h:94,label:'W'.repeat(180),role:'기업',note:'근거'})`,context);
assert.ok(card.includes('width="147" height="74"'));
assert.ok(card.includes('class="graph-card-text"'));
assert.ok(!card.includes('<tspan'));
console.log('All related stories, descending score, safe titles and bounded SVG text passed.');

node('#issue-search').value='';node('#issue-level').value='all';
vm.runInContext(`networkMeaning=false;graphMode='network';selectedIssue=data.issues[0].id;renderFocus();`,context);
assert.ok(node('#surface').innerHTML.includes('기업과 테마의 공동 보도'));
vm.runInContext(`networkKind='all';globalThis.g=semanticGraph([{article_ids:['one','two'],links:[{symbol:'AAA',article_ids:['one','two']},{symbol:'BBB',article_ids:['one']},{symbol:'CCC',article_ids:['two']}],themes:[{name:'AI',article_ids:['one']}]}]);`,context);
assert.equal(context.g.nodes.length,4);
assert.equal(context.g.edges.length,4,'Shared article connects companies and themes without connecting disjoint articles');
assert.ok(!context.g.edges.some(e=>[e.left.label,e.right.label].sort().join(',')==='BBB,CCC'));
assert.equal(context.g.nodes.find(n=>n.label==='AAA').article_ids.length,2);
vm.runInContext(`globalThis.positions=semanticPositions(g.nodes,g.edges);networkView={x:0,y:0,w:1040,h:680};zoomNetwork(.5);`,context);
assert.equal(vm.runInContext('networkView.w',context),520);
assert.equal(vm.runInContext('networkView.x',context),260);
assert.ok(context.positions.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
console.log('Semantic node merging, within-article evidence, force layout and zoom passed.');

vm.runInContext(`data.articles=[{id:'later',sentiment:{label:'neutral'},title:'Later <unsafe>',url:'javascript:alert(1)',published_at:'2026-09-14T02:00:00Z',summary:'Later summary',source:'Wire',official_source:true},{id:'first',sentiment:{label:'neutral'},title:'First report',url:'https://example.com/first',published_at:'2026-09-14T01:00:00Z',source:'News'}];data.issues[0].article_ids=['later','first','later'];selectedIssue=data.issues[0].id;graphMode='flow';renderFocus();`,context);
assert.deepEqual(Array.from(vm.runInContext('flowArticles(currentIssue()).map(a=>a.id)',context)),['first','later']);
assert.ok(node('#surface').innerHTML.indexOf('First report')<node('#surface').innerHTML.indexOf('Later &lt;unsafe&gt;'));
assert.ok(!node('#surface').innerHTML.includes('javascript:'));
assert.ok(node('#surface').innerHTML.includes('공식 출처'));
assert.ok(node('#surface').innerHTML.includes('같은 기업의 다른 사건'));
assert.ok(node('#map-tabs').innerHTML.includes('홈에서 관계 보기'));
vm.runInContext(`handleTarget({dataset:{issue:selectedIssue}});`,context);
assert.equal(vm.runInContext('graphMode',context),'flow','Selecting another issue preserves the flow view');
console.log('Story flow chronology, deduplication, source safety and view persistence passed.');
vm.runInContext(`networkMeaning=true;data.articles=[{id:'semantic',semantic_status:'grounded',themes:{'배달·서비스 경쟁':['quote']},semantic_relations:[{source:'Walmart',target:'DoorDash',source_key:'WMT',target_key:'DASH',kind:'competition',quote:'Walmart competes with DoorDash.'}]},{id:'old',semantic_status:'pending',themes:{'정책·지정학':['war']},semantic_relations:[]}];globalThis.mg=meaningGraph([{article_ids:['semantic','old']}]);`,context);
assert.equal(context.mg.edges.length,1);
assert.equal(context.mg.edges[0].kind,'competition');
assert.ok(context.mg.nodes.some(n=>n.label==='DASH'));
assert.ok(!context.mg.nodes.some(n=>n.label==='정책·지정학'));
console.log('Meaning graph uses grounded model records and excludes legacy keyword edges.');

// Global navigation must not inherit the story-list filter or require a selected story.
node('#network-search').value='';node('#issue-search').value='no matching story';
vm.runInContext(`data.articles=[{id:'a',title:'A & B',source:'Wire',url:'https://example.com/a',published_at:'2026-09-14T01:00:00Z',summary:'Source',sentiment:{label:'neutral'},links:[]}];data.issues=[{id:'story-a',article_ids:['a'],title:'A & B',category:'계약',links:[{symbol:'AAA',name:'A',article_ids:['a']},{symbol:'BBB',name:'B',article_ids:['a']}],themes:[],sources:['Wire'],sentiments:{neutral:1},reasons:[]}];networkMeaning=false;networkKind='all';selectedIssue='';changeView('network');`,context);
assert.equal(vm.runInContext('networkIssues().length',context),1);
assert.equal(vm.runInContext('visibleIssues().length',context),0);
assert.ok(node('#surface').innerHTML.includes('semantic-svg'));
assert.equal(node('#map-tabs').innerHTML,'');
vm.runInContext(`selectedEdge='net:node:company:AAA';renderFocus();`,context);
assert.ok(node('#dossier').innerHTML.includes('data-open-story="a"'));
vm.runInContext(`renderIssues();`,context);
assert.equal(vm.runInContext('selectedEdge',context),'net:node:company:AAA','Refreshing a filtered story list must not clear a network selection');
vm.runInContext(`openStory('a');`,context);
assert.equal(vm.runInContext('activeView',context),'radar');
assert.equal(vm.runInContext('selectedIssue',context),'story-a');
assert.ok(node('#surface').innerHTML.includes('story-flow'));
assert.ok(node('#map-tabs').innerHTML.includes('홈에서 관계 보기'));
node('#network-search').value='does-not-exist';
vm.runInContext(`changeView('network');`,context);
assert.equal(vm.runInContext('networkIssues().length',context),0);
assert.ok(node('#dossier').innerHTML.includes('검색어를 지우거나'));
console.log('Independent global network, empty state and source-to-story navigation passed.');
// Universe sorting: all four metrics, both directions, missing values always last.
for(const key of ['market_cap','count','high','sentiment']){
 context.sortFixture=[{symbol:'A',market_cap:10,count:1,sentiment:-0.5},{symbol:'B',market_cap:20,count:2,sentiment:0.5},{symbol:'Z',count:0,sentiment:0}];
 context.sortArticles=[{importance:{high:true},links:[{symbol:'A'}]},{importance:{high:true},links:[{symbol:'B'}]},{importance:{high:true},links:[{symbol:'B'}]}];
 const asc=vm.runInContext(`sortedStocks(sortFixture,sortArticles,'${key}','asc').map(s=>s.symbol).join(',')`,context);
 const desc=vm.runInContext(`sortedStocks(sortFixture,sortArticles,'${key}','desc').map(s=>s.symbol).join(',')`,context);
 assert.equal(asc,['count','high'].includes(key)?'Z,A,B':'A,B,Z');
 assert.equal(desc,'B,A,Z');
 assert.equal(context.sortFixture[0].symbol,'A');
}
console.log('Universe sorting: four metrics, both directions and missing values passed.');
context.sectorStocks=[{symbol:'A',sector:'Technology'},{symbol:'B',sector:'Technology'},{symbol:'C',sector:'Finance'},{symbol:'D'}];
context.sectorArticles=[{id:'one',links:[{symbol:'A'},{symbol:'B'},{symbol:'C'}],sentiment:{score:.8},importance:{high:true}},{id:'two',links:[{symbol:'A'}],sentiment:{score:-.3}},{id:'three',links:[{symbol:'D'}],sentiment:{score:0}}];
const sectorResult=JSON.parse(vm.runInContext('JSON.stringify(sectorRows(sectorStocks,sectorArticles))',context));
assert.equal(sectorResult.find(s=>s.sector==='Technology').count,2);
assert.equal(sectorResult.find(s=>s.sector==='Technology').total,.5);
assert.equal(sectorResult.find(s=>s.sector==='Technology').high,1);
assert.equal(sectorResult.find(s=>s.sector==='Finance').total,.8);
assert.equal(sectorResult.find(s=>s.sector==='Unclassified').count,1);
vm.runInContext("data={stocks:sectorStocks,articles:sectorArticles,issues:[{id:'one',title:'A',links:[],article_ids:['one']},{id:'three',title:'D',links:[],article_ids:['three']}]}; selectedSector='Technology';",context);
node('#network-search').value='';
assert.equal(vm.runInContext('networkIssues().map(i=>i.id).join() ',context),'one');
vm.runInContext("selectedSector='';",context);
console.log('Sector totals, cross-sector attribution, unknown classification and map filtering passed.');

vm.runInContext(`data.articles=[{id:'a',title:'Same title',summary:'Same summary',published_at:'2026-09-14T00:00:00Z'},{id:'b',title:'Same title',summary:'Same summary',published_at:'2026-09-14T01:00:00Z'},{id:'c',title:'Same title',summary:'Changed facts',published_at:'2026-09-14T02:00:00Z'}];globalThis.grouped=groupedFlowArticles({article_ids:['a','b','c']});`,context);
assert.equal(context.grouped.length,2);
assert.equal(context.grouped[0].duplicates[0].id,'b');
assert.equal(context.grouped[1].lead.id,'c','Changed summary must not be collapsed as duplicate');
console.log('Reader duplicate folding retains distinct follow-up content.');

// Evidence uses the same signed sentiment score as the article archive.
for(const [label,score,text] of [['positive',.72,'긍정 +0.72'],['negative',-.4,'부정 -0.40'],['neutral',0,'중립 0.00']]){
 context.sentimentArticle={sentiment:{label,score}};
 assert.ok(vm.runInContext('sentimentTag(sentimentArticle)',context).includes(text));
}
for(const sentiment of [undefined,{label:'neutral',score:null},{label:'positive',score:NaN},{label:'<bad>',score:1}]){
 context.sentimentArticle={sentiment};
 assert.ok(vm.runInContext('sentimentTag(sentimentArticle)',context).includes('미평가'));
}
vm.runInContext(`data.articles=[{id:'sent',title:'Evidence',source:'test',published_at:'2026-09-17T00:00:00Z',sentiment:{label:'negative',score:-.4}}];semanticNodes=[{key:'LLY',label:'LLY',article_ids:['sent']}];networkEdges=[{key:'net:test',left:{label:'LLY'},right:{label:'Other'},article_ids:['sent']}];selectedEdge='net:node:LLY';renderEventEvidence();`,context);
assert.ok(node('#edge-evidence').innerHTML.includes('부정 -0.40'));
vm.runInContext("selectedEdge='net:test';renderEventEvidence();",context);
assert.ok(node('#edge-evidence').innerHTML.includes('부정 -0.40'));
console.log('Article sentiment labels, missing values and node/edge evidence passed.');
