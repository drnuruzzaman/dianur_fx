
function lastSunday(year, month){const d=new Date(Date.UTC(year,month,0));d.setUTCDate(d.getUTCDate()-d.getUTCDay());return d;}
function brokerHour(msUtc){const d=new Date(msUtc);const y=d.getUTCFullYear();
const s=lastSunday(y,3); s.setUTCHours(1,0,0,0); const e=lastSunday(y,10); e.setUTCHours(1,0,0,0);
const summer=d>=s&&d<e; return new Date(msUtc+(summer?3:2)*3600000).getUTCHours();}
const xs=JSON.parse(process.argv[1]); console.log(JSON.stringify(xs.map(brokerHour)));
