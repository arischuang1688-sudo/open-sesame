import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const pages=['index.html','app-v2.html'];
const html=Object.fromEntries(pages.map(page=>[page,readFileSync(new URL(`../${page}`,import.meta.url),'utf8')]));
const storage=()=>({getItem:key=>key==='os_pv'?'1':null,setItem(){}});
const stock=overrides=>({code:'1001',name:'測試股',close:100,change:1,change_pct:1,volume:100,
  priority:false,cond_count:0,conditions:{ma:false,kd:false,trust:false,market:false},
  pe_ratio:null,pe_under_20:false,pe_date:'2026-10-16',...overrides});
const dashboard=stocks=>({updated_at:'2026-10-16T13:05:00Z',
  source_dates:{stock_quote:'2026-10-16',pe_ratio:'2026-10-16'},market:{},stocks});

async function render(page,data){
  const elements=Object.fromEntries(['summary','sourceDates','tbody'].map(key=>[key,{innerHTML:''}]));
  const script=html[page].match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(script,{
    document:{getElementById:key=>elements[key]},sessionStorage:storage(),localStorage:storage(),
    fetch:async url=>{
      assert.match(String(url),/^data\/dashboard\.json\?ts=/);
      return {ok:true,json:async()=>data};
    },console
  },{filename:page});
  await new Promise(resolve=>setImmediate(resolve));
  const rows=[...elements.tbody.innerHTML.matchAll(/<tr>([\s\S]*?)<\/tr>/g)]
    .map(([,row])=>[...row.matchAll(/<td\b([^>]*)>([\s\S]*?)<\/td>/g)]
      .map(([,attributes,content])=>({attributes,content})));
  return {elements,rows};
}

for(const page of pages){
  test(`${page}: unknown and legacy uncomputed signals are not failed signals or technical ranks`,async()=>{
    const legacy=stock({code:'1001',month_week:'僅前30名計算',kd:'僅前30名計算',
      relative:'僅前30名計算',priority:true,cond_count:4});
    const unknown=stock({code:'1002',conditions:{ma:null,kd:null,trust:true,market:false}});
    const failed=stock({code:'1003'});
    const {elements,rows}=await render(page,dashboard([legacy,unknown,failed]));
    assert.deepEqual(rows.map(row=>row[1].content.match(/<b>(\d+)/)[1]),['1003','1001','1002']);
    assert.deepEqual(rows.map(row=>row[0].content),['1','—','—']);
    assert.match(rows[0][11].content,/✗月週線↑/);
    assert.match(rows[0][11].content,/✗KD交叉/);
    for(const row of rows.slice(1)){
      assert.match(row[11].content,/—月週線↑（待計算）/);
      assert.match(row[11].content,/—KD交叉（待計算）/);
      assert.doesNotMatch(row[11].content,/✗(?:月週線↑|KD交叉)|tag pri/);
      assert.match(row[11].content,/待評估/);
      assert.match(row[11].content,/已確認 2\/4 項/);
    }
    for(const column of [7,8,9])assert.equal(rows[1][column].content,'尚未計算');
    assert.match(elements.tbody.innerHTML,/完整訊號組（1 檔）/);
    assert.match(elements.tbody.innerHTML,/待評估組（2 檔）/);
    assert.match(elements.summary.innerHTML,/四項訊號完整 1 檔・待評估 2 檔/);
    assert.match(elements.summary.innerHTML,/⭐ 優先買入<\/div><div class="big">0 檔/);
  });

  test(`${page}: pending stocks use P/E then amount rather than partial passes`,async()=>{
    const {rows,elements}=await render(page,dashboard([
      stock({code:'1001',pe_ratio:20.01,pe_under_20:false,amount:1000,
        conditions:{ma:null,kd:true,trust:true,market:true}}),
      stock({code:'1002',pe_ratio:20,pe_under_20:true,amount:1,
        conditions:{ma:null,kd:null,trust:null,market:null}}),
      stock({code:'1003',pe_ratio:18,pe_under_20:true,amount:100,
        conditions:{ma:null,kd:null,trust:true,market:true}})
    ]));
    assert.deepEqual(rows.map(row=>row[1].content.match(/<b>(\d+)/)[1]),['1003','1002','1001']);
    assert.ok(rows.every(row=>row[0].content==='—'));
    assert.doesNotMatch(elements.tbody.innerHTML,/完整訊號組（/);
    assert.match(elements.tbody.innerHTML,/待評估組（3 檔）/);
    assert.match(rows[1][11].content,/已確認 0\/4 項・符合 0 項/);
  });

  test(`${page}: missing conditions and insufficient history remain unknown`,async()=>{
    const {rows}=await render(page,dashboard([
      stock({code:'1001',conditions:undefined,priority:true}),
      stock({code:'1002',month_week:'資料不足',kd:'資料待補'}),
      stock({code:'1003',conditions:{ma:true,kd:true,trust:'false',market:1}})
    ]));
    assert.ok(rows.every(row=>row[0].content==='—'));
    assert.ok(rows.every(row=>!row[11].content.includes('tag pri')));
    assert.match(rows[0][11].content,/已確認 0\/4 項/);
    assert.match(rows[1][11].content,/已確認 2\/4 項/);
    assert.match(rows[2][11].content,/—投信買超（待計算）/);
    assert.match(rows[2][11].content,/—大盤轉強（待計算）/);
  });

  test(`${page}: P/E values and summary require valid numbers and a validated inclusive threshold`,async()=>{
    const values=[
      {pe_ratio:12.5,pe_under_20:true},
      {pe_ratio:20,pe_under_20:true},
      {pe_ratio:20.01,pe_under_20:true},
      {pe_ratio:null,pe_under_20:true},
      {pe_ratio:0,pe_under_20:true},
      {pe_ratio:-10,pe_under_20:true},
      {pe_ratio:Infinity,pe_under_20:true},
      {pe_ratio:NaN,pe_under_20:true},
      {pe_ratio:undefined,pe_under_20:true},
      {pe_ratio:'15',pe_under_20:true},
      {pe_ratio:19,pe_under_20:false},
      {pe_ratio:18,pe_under_20:undefined},
      {pe_ratio:17,pe_under_20:'true'}
    ];
    const stocks=values.map((value,i)=>stock({code:String(1000+i),...value}));
    delete stocks[8].pe_ratio;
    const {elements,rows}=await render(page,dashboard(stocks));
    assert.equal(rows.length,values.length);
    for(const row of rows){assert.equal(row.length,13);assert.equal(row[3].attributes,' class="pe-cell"')}
    assert.match(rows[0][3].content,/^12\.50 倍/);
    assert.match(rows[1][3].content,/^20\.00 倍/);
    for(const i of [0,1])assert.match(rows[i][3].content,/<span class="tag pe">20倍內<\/span>/);
    assert.equal(rows[2][3].content,'20.01 倍');
    for(const i of [3,4,5,6,7,8,9]){
      assert.equal(rows[i][3].content,'<span class="muted">未提供／不適用</span>');
    }
    assert.equal(rows[10][3].content,'19.00 倍');
    assert.equal(rows[11][3].content,'18.00 倍');
    assert.equal(rows[12][3].content,'17.00 倍');
    assert.equal((elements.tbody.innerHTML.match(/class="tag pe"/g)||[]).length,2);
    assert.match(elements.summary.innerHTML,/本益比20倍內<\/div><div class="big">2 檔<\/div>/);
    assert.match(elements.summary.innerHTML,/⭐ 優先買入<\/div><div class="big">0 檔<\/div>/);
  });

  test(`${page}: preserves supplied ranking and the original four priority signals`,async()=>{
    const stocks=Object.freeze([
      Object.freeze(stock({code:'9003',pe_ratio:10,pe_under_20:true,cond_count:0})),
      Object.freeze(stock({code:'1001',pe_ratio:30,priority:true,cond_count:4,
        conditions:{ma:true,kd:true,trust:true,market:true}})),
      Object.freeze(stock({code:'2002',pe_ratio:15,pe_under_20:true,cond_count:3,
        conditions:{ma:true,kd:true,trust:true,market:false}}))
    ]);
    const data=dashboard(stocks),before=structuredClone(data);
    const {elements,rows}=await render(page,data);
    assert.deepEqual(rows.map(row=>row[1].content.match(/<b>(\d+)/)[1]),['9003','1001','2002']);
    assert.deepEqual(rows.map(row=>row[0].content),['1','2','3']);
    assert.deepEqual(data,before);
    assert.doesNotMatch(rows[0][11].content,/class="tag pri"/);
    assert.match(rows[1][11].content,/class="tag pri">⭐ 優先買入/);
    assert.doesNotMatch(rows[2][11].content,/class="tag pri"/);
    for(const row of rows){
      assert.equal((row[11].content.match(/class="chip /g)||[]).length,4);
      for(const name of ['月週線↑','KD交叉','投信買超','大盤轉強'])assert.ok(row[11].content.includes(name));
    }
    assert.match(elements.summary.innerHTML,/⭐ 優先買入<\/div><div class="big">1 檔<\/div>/);
  });

  test(`${page}: keeps Taiwan quote colors red for gains and green for losses`,async()=>{
    const {rows}=await render(page,dashboard([
      stock({code:'1001',change:3,change_pct:3}),
      stock({code:'1002',change:-2,change_pct:-2}),
      stock({code:'1003',change:0,change_pct:0})
    ]));
    assert.equal(rows[0][4].attributes,' class="r"');
    assert.equal(rows[0][4].content,'+3 (+3%)');
    assert.equal(rows[1][4].attributes,' class="g"');
    assert.equal(rows[1][4].content,'-2 (-2%)');
    assert.equal(rows[2][4].attributes,' class=""');
    assert.match(html[page],/\.r\{color:#b42318;/);
    assert.match(html[page],/\.g\{color:#16803c;/);
  });

  test(`${page}: explains the ranking, reports the P/E source date and has a concise version record`,async()=>{
    const data=dashboard([]);
    const {elements}=await render(page,data);
    assert.match(elements.sourceDates.innerHTML,/本益比：<b>2026-10-16<\/b>/);
    delete data.source_dates.pe_ratio;
    const withoutDate=await render(page,data);
    assert.match(withoutDate.elements.sourceDates.innerHTML,/本益比：<b>—<\/b>/);
    assert.match(html[page],/<th>收盤<\/th><th>本益比<\/th><th>漲跌<\/th>/);
    assert.match(html[page],/原有優先訊號 → 條件數 → 本益比20倍內 → 成交金額/);
    assert.match(html[page],/0 &lt; 本益比 ≤ 20/);
    assert.match(html[page],/未提供／不適用不加分/);
    assert.match(html[page],/(?:正式版|版本) 2026-10-29/);
    const record=html[page].match(/程式進版修改紀錄<\/b><div>([^<]+)<\/div>/)[1];
    assert.ok(record.length<=100);
    assert.match(record,/^2026-10-29：/);
    assert.doesNotMatch(html[page],/colspan="12"/);
  });
}
