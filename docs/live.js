/* live.js — modularized client-side loader for current-week status */

const Live = (function(){
  const BASE = 'https://api.sleeper.app/v1';
  const ESPN = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard';
  const SLEEPER_TEAM_ABBR_TO_ESPN = {'WAS':'WSH'};

  async function jfetch(url){
    const r = await fetch(url);
    if(!r.ok) throw new Error('fetch failed: '+url+' ('+r.status+')');
    return await r.json();
  }

  async function fetchSleeperBasics(leagueId){
    const users = await jfetch(`${BASE}/league/${leagueId}/users`);
    const userMap = {};
    (users||[]).forEach(u=> userMap[u.user_id]=u.display_name||u.username||'Unknown');

    const rosters = await jfetch(`${BASE}/league/${leagueId}/rosters`);
    const rosterToOwner = {};
    (rosters||[]).forEach(r=> rosterToOwner[r.roster_id]= userMap[r.owner_id]||`Roster ${r.roster_id}`);

    const players = await jfetch(`${BASE}/players/nfl`);
    return { userMap, rosterToOwner, players };
  }

  async function fetchEspnGames(week, seasonType){
    const espnSeason = seasonType === 'pre' ? 1 : (seasonType === 'post' ? 3 : 2);
    const data = await jfetch(`${ESPN}?seasontype=${espnSeason}&week=${week}`);
    const teamGames = {};
    (data.events||[]).forEach(event=>{
      const status = event.status||{};
      const st = status.type||{};
      const state = st.state || null;
      const completed = st.completed||false;
      const detail = st.detail||st.description||'';
      const clock = status.displayClock||'';
      const period = status.period||0;
      let urgency = 9999;
      if(state==='in'){
        let remaining = 0;
        try{ if(clock && clock.indexOf(':')!==-1){ const [m,s]=clock.split(':'); remaining = parseInt(m||'0',10)*60+parseInt(s||'0',10);} }
        catch(e){ remaining=0 }
        const period_weight = {1:4000,2:3000,3:2000,4:1000}[period]||500;
        urgency = period_weight + remaining;
      }
      const comps = (event.competitions&&event.competitions[0]&&event.competitions[0].competitors)||[];
      comps.forEach(c=>{ const t=c.team||{}; if(t.abbreviation) teamGames[t.abbreviation]={state,completed,detail,clock,period,urgency,name:event.name||'',short_name:event.shortName||''}; });
    });
    return teamGames;
  }

  function mapTeam(abbr){ return SLEEPER_TEAM_ABBR_TO_ESPN[abbr]||abbr; }

  function computeEntries(matchups, rosterToOwner, players, teamGames){
    const confirmed = [];
    const inProgress = [];
    const pendingEmpty = [];

    (matchups||[]).forEach(m=>{
      const roster_id = m.roster_id;
      const owner = rosterToOwner[roster_id]||`Roster ${roster_id}`;
      const starters = m.starters||[];
      const players_points = m.players_points||{};
      starters.forEach((starter_id,idx)=>{
        if(!starter_id || starter_id==='0' || starter_id==='null' || starter_id===null){
          pendingEmpty.push({owner,roster_id,slot:idx+1});
          return;
        }
        let pts = players_points[String(starter_id)];
        if(pts==null) pts=0.0;
        if(pts>0) return;
        const p = players[String(starter_id)]||{};
        const name = (p.full_name || ((p.first_name||'')+' '+(p.last_name||''))).trim()||`Player ${starter_id}`;
        const pos = p.position||'?';
        const team = mapTeam(p.team||'?');
        const game = teamGames && teamGames[team] ? teamGames[team] : {};
        const game_state = game.state||'unknown';
        const completed_flag = game.completed||false;
        const entry = {owner,roster_id,player:name,position:pos,nfl_team:team,points:pts,player_id:String(starter_id),game_detail:game.detail||'',clock:game.clock||'',period:game.period||0,urgency:game.urgency||9999,game_name:game.short_name||game.name||''};
        if(completed_flag || game_state==='post') confirmed.push(entry);
        else if(game_state==='in') inProgress.push(entry);
      });
    });

    inProgress.sort((a,b)=> (a.urgency||9999)-(b.urgency||9999));
    return { confirmed, inProgress, pendingEmpty };
  }

  function renderRows(tbodyId, rowsHtml, emptyCols){
    const tbody = document.getElementById(tbodyId);
    if(!tbody) return;
    if(rowsHtml && rowsHtml.length) tbody.innerHTML = rowsHtml; else tbody.innerHTML = `<tr><td colspan="${emptyCols}" class="empty">No rows</td></tr>`;
  }

  function updateMetaTimestamp(){
    const meta = document.querySelector('.meta');
    if(!meta) return;
    meta.innerHTML = meta.innerHTML.replace(/Last updated:.*/,'Last updated: '+(new Date().toISOString().replace('T',' ').slice(0,19)+' UTC'));
  }

  function computeStatusStyle(entry){
    const period = entry.period || 0;
    const clock = entry.clock || '';
    let remaining = 0;
    try{
      if(clock && clock.indexOf(':') !== -1){
        const parts = clock.split(':');
        const mins = parseInt(parts[0]||'0',10) || 0;
        const secs = parseInt(parts[1]||'0',10) || 0;
        remaining = mins * 60 + secs;
      }
    }catch(e){ remaining = 0; }
    const max_secs = 15 * 60;
    let progress = 1 - (remaining / max_secs);
    if(!isFinite(progress)) progress = 0;
    progress = Math.max(0, Math.min(1, progress));
    if(period === 3){
      const alpha = 0.15 + (progress * 0.40);
      return `background: rgba(255, 200, 30, ${alpha.toFixed(2)});`;
    } else if(period >= 4){
      const alpha = 0.20 + (progress * 0.50);
      return `background: rgba(255, 50, 50, ${alpha.toFixed(2)});`;
    }
    return '';
  }

  function renderLeaderboard(siteData, confirmed, inProgress, pendingEmpty, currentWeek){
    const history = (siteData && siteData.history) || {};
    const finalizedWeeks = (siteData && siteData.finalized_weeks) || Object.keys(history).map(x=>parseInt(x,10));
    // ensure currentWeek is present in header (as provisional)
    const weeksSet = new Set(finalizedWeeks.map(Number));
    if(currentWeek) weeksSet.add(Number(currentWeek));
    const weeks = Array.from(weeksSet).sort((a,b)=>b-a);

    // build counts from history
    const counts = {};
    Object.entries(history||{}).forEach(([wk, wkdata])=>{
      const wnum = parseInt(wk,10);
      (wkdata.ices||[]).forEach(ice=>{ counts[ice.owner] = counts[ice.owner] || {}; counts[ice.owner][wnum] = (counts[ice.owner][wnum]||0)+1; counts[ice.owner].total = (counts[ice.owner].total||0)+1; });
      (wkdata.empty_slots||[]).forEach(e=>{ counts[e.owner] = counts[e.owner] || {}; counts[e.owner][wnum] = (counts[e.owner][wnum]||0)+1; counts[e.owner].total = (counts[e.owner].total||0)+1; });
    });

    // Add current-week counts only for confirmed (games over) entries.
    // If the current week is already present in `history` (server finalized),
    // do not add provisional current-week counts to avoid double-counting.
    const weekKey = String(currentWeek);
    const historyHasWeek = history && Object.prototype.hasOwnProperty.call(history, weekKey);
    if(!historyHasWeek){
      const currentConfirmed = confirmed || [];
      currentConfirmed.forEach(e=>{
        const owner = e.owner;
        counts[owner] = counts[owner] || {};
        counts[owner][currentWeek] = (counts[owner][currentWeek]||0) + 1;
        counts[owner].total = (counts[owner].total||0) + 1;
      });
    }

    // build table header
    const head = document.getElementById('leaderboard-head');
    if(head){
      const ths = ['<th>Owner</th>'].concat(weeks.map(w=>`<th>W${w}</th>`)).concat(['<th>Total</th>']).join('');
      head.innerHTML = `<tr>${ths}</tr>`;
    }

    // build rows sorted by total
    const owners = Object.keys(counts).sort((a,b)=> (counts[b].total||0)-(counts[a].total||0));
    const rows = owners.map(owner=>{
      const rowCells = weeks.map(w=>`<td>${counts[owner][w]||'–'}</td>`).join('');
      return `<tr><td><strong>${owner}</strong></td>${rowCells}<td class='total'>${counts[owner].total||0}</td></tr>`;
    }).join('') || `<tr><td colspan='20' class='empty'>No ices yet</td></tr>`;

    const body = document.getElementById('leaderboard-body');
    if(body) body.innerHTML = rows;
  }

  async function init(){
    try{
      // Load committed data.json first (historical finalized weeks + metadata)
      let siteData = {};
      try{
        siteData = await jfetch('data.json');
      }catch(e){ /* ignore: site may not have data.json */ }

      const meta = document.querySelector('.meta');
      const LEAGUE_ID = (siteData && siteData.league_id) || (meta && meta.dataset.leagueId) || '';
      const week = parseInt((siteData && siteData.current_week) || (meta && meta.dataset.currentWeek) || '0',10);
      const seasonType = (siteData && siteData.season_type) || (meta && meta.dataset.seasonType) || 'regular';

      // Update meta display from committed data if available
      if(meta && siteData && siteData.season){
        meta.dataset.leagueId = siteData.league_id || '';
        meta.dataset.currentWeek = siteData.current_week || '';
        meta.dataset.seasonType = siteData.season_type || '';
        meta.innerHTML = `Season ${siteData.season} · Week ${siteData.current_week}<br>Last updated: ${(siteData.updated_at||'').replace('T',' ').slice(0,19)+' UTC'}`;
      }

      // Update week display in section headings
      const confirmedWeekSpan = document.getElementById('confirmed-week');
      const inprogressWeekSpan = document.getElementById('inprogress-week');
      if(confirmedWeekSpan) confirmedWeekSpan.textContent = `— Week ${week}`;
      if(inprogressWeekSpan) inprogressWeekSpan.textContent = `— Week ${week}`;

      const { userMap, rosterToOwner, players } = await fetchSleeperBasics(LEAGUE_ID);
      const matchups = await jfetch(`${BASE}/league/${LEAGUE_ID}/matchups/${week}`);
      const teamGames = await fetchEspnGames(week, seasonType);

      const { confirmed, inProgress, pendingEmpty } = computeEntries(matchups, rosterToOwner, players, teamGames);

      updateMetaTimestamp();

      const confHtml = (confirmed||[]).map(i=>{ const cls = i.points<0?'neg':'zero'; return `<tr><td>${i.owner}</td><td>${i.player}</td><td>${i.position}</td><td>${i.nfl_team}</td><td class="${cls}">${i.points}</td></tr>`; }).join('');
      // Render confirmed: if empty show a friendly message
      const confirmedWrap = document.getElementById('confirmed-wrap');
      if(confirmedWrap){
        if(confHtml && confHtml.length){
          confirmedWrap.innerHTML = `<table><thead><tr><th>Owner</th><th>Player</th><th>Pos</th><th>Team</th><th>Pts</th></tr></thead><tbody id="confirmed-tbody">${confHtml}</tbody></table>`;
        } else {
          confirmedWrap.innerHTML = `<p class="empty">None yet</p>`;
        }
      }

      const inprogHtml = (inProgress||[]).map(i=>{ const cls = i.points<0?'neg':'zero'; const detail = i.game_detail||i.clock||''; const style = computeStatusStyle(i); return `<tr><td>${i.owner}</td><td>${i.player}</td><td>${i.position}</td><td>${i.nfl_team}</td><td class="${cls}">${i.points}</td><td style="${style}">${detail}</td></tr>`; }).join('');
      // include pending empties as well
      const pendingHtml = (pendingEmpty||[]).map(e=>`<tr><td>${e.owner}</td><td colspan="3"><em>Empty starter slot #${e.slot}</em></td><td class="zero">0</td><td style="background: rgba(255, 200, 30, 0.25);">Pending – fills when week ends</td></tr>`).join('');
      const inprogressWrap = document.getElementById('inprogress-wrap');
      if(inprogressWrap){
        if(inprogHtml.length || pendingHtml.length){
          inprogressWrap.innerHTML = `<table><thead><tr><th>Owner</th><th>Player</th><th>Pos</th><th>Team</th><th>Pts</th><th>Game Status</th></tr></thead><tbody id="inprogress-tbody">${inprogHtml+pendingHtml}</tbody></table>`;
        } else {
          inprogressWrap.innerHTML = `<p class="empty">None yet</p>`;
        }
      }

      // Render leaderboard by merging historical finalized weeks with this current-week entries
      renderLeaderboard(siteData, confirmed, inProgress, pendingEmpty, week);

    }catch(e){ console.warn('live init failed',e); }
  }

  return { init };
})();

if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', ()=>Live.init()); else Live.init();
