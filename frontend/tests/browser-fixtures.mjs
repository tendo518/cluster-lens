// Test-only boundary fixtures. Production exports nothing on window: these
// helpers operate through public controls and the same-origin fetch boundary.
export async function refresh(evaluate, trigger = "document.querySelector('#refresh-button').click()") {
  await evaluate(`(async () => {
    const button = document.querySelector('#refresh-button');
    if (button.disabled) throw new Error('Fixture requires idle refresh');
    ${trigger};
    if (!button.disabled) throw new Error('Refresh trigger did not start a request');
    const deadline = performance.now() + 15000;
    while (button.disabled) {
      if (performance.now() > deadline) throw new Error('Refresh did not finish');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
  })()`);
}
export async function readOverview(evaluate) {
  return evaluate(`(async () => {
    const token = localStorage.getItem('hqlab-monitor-token') || sessionStorage.getItem('hqlab-monitor-token');
    const response = await fetch('/api/overview', {headers: token ? {Authorization: 'Bearer ' + token} : {}});
    if (!response.ok) throw new Error('Fixture overview: ' + response.status);
    return response.json();
  })()`);
}
export async function setOverviewFixture(evaluate, overview) {
  await evaluate(`{
    if (!window.fixtureOriginalFetch) {
      window.fixtureOriginalFetch = window.fetch;
      window.fetch = (...args) => String(args[0]).startsWith('/api/overview')
        ? Promise.resolve(new Response(JSON.stringify(window.overviewFixture), {status:200}))
        : window.fixtureOriginalFetch(...args);
    }
    window.overviewFixture = ${JSON.stringify(overview)};
  }`);
}
export async function restoreOverviewFixture(evaluate) {
  await evaluate(`{
    if (window.fixtureOriginalFetch) window.fetch = window.fixtureOriginalFetch;
    delete window.fixtureOriginalFetch; delete window.overviewFixture;
  }`);
}
