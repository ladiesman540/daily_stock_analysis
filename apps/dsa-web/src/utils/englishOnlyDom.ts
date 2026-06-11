const HAN_RE = /[\u3400-\u9fff]/;

const EXACT_TRANSLATIONS: Record<string, string> = {
  推送通知: 'Notifications',
  分析: 'Analyze',
  分析中: 'Analyzing',
  历史分析: 'History',
  暂无历史分析记录: 'No historical reports',
  '完成首次分析后，这里会保留最近结果。': 'After your first analysis, recent results will appear here.',
  开始分析: 'Start Analysis',
  '输入股票代码进行分析，或从左侧选择历史报告查看。': 'Enter a ticker to analyze, or select a historical report from the sidebar.',
  持仓管理: 'Portfolio',
  '组合快照、手工录入、CSV 导入与风险分析（支持全组合 / 单账户切换）': 'Portfolio snapshot, manual entries, CSV import, and risk analysis.',
  '还没有可用账户，请先创建账户后再录入交易或导入 CSV。': 'No accounts yet. Create an account before entering trades or importing CSV.',
  新建账户: 'New Account',
  创建账户: 'Create Account',
  '创建后自动切换到该账户': 'The app will switch to the new account after creation.',
  总权益: 'Total Equity',
  总市值: 'Market Value',
  总现金: 'Cash',
  汇率状态: 'FX Status',
  刷新汇率: 'Refresh FX',
  最新: 'Current',
  过期: 'Stale',
  持仓明细: 'Positions',
  当前无持仓数据: 'No positions yet',
  '录入交易或导入 CSV 后，这里会展示按账户汇总的持仓明细。': 'Enter trades or import CSVs to show account-level positions here.',
  暂无集中度数据: 'No concentration data',
  '风险模块完成计算后，这里会展示行业或个股维度的集中度分布。': 'Concentration by sector or position appears here after risk data is calculated.',
  回撤监控: 'Drawdown Monitor',
  最大回撤: 'Max drawdown',
  当前回撤: 'Current drawdown',
  止损接近预警: 'Stop-Loss Proximity',
  触发数: 'Triggered',
  接近数: 'Near',
  告警: 'Alert',
  口径: 'Scope',
  账户数: 'Accounts',
  计价币种: 'Currency',
  成本法: 'Cost Method',
  手工录入: 'Manual Entry',
  交易: 'Trades',
  资金流水: 'Cash Ledger',
  公司行为: 'Corporate Actions',
  买入: 'Buy',
  卖出: 'Sell',
  流入: 'Inflow',
  流出: 'Outflow',
  现金分红: 'Cash Dividend',
  拆并股调整: 'Split Adjustment',
  提交交易: 'Submit Trade',
  提交资金流水: 'Submit Cash Entry',
  提交企业行为: 'Submit Corporate Action',
  选择: 'Choose',
  选择文件: 'Choose File',
  解析: 'Parse',
  选择图片: 'Choose Image',
  解析文件: 'Parse File',
  提交导入: 'Commit Import',
  事件记录: 'Event Log',
  刷新流水: 'Refresh Events',
  暂无流水: 'No events',
  上一页: 'Previous',
  下一页: 'Next',
  系统设置: 'Settings',
  '统一管理模型、数据源、通知、安全认证与导入能力。': 'Manage models, data sources, notifications, authentication, and import tools.',
  重置: 'Reset',
  保存配置: 'Save Config',
  配置分类: 'Categories',
  '按模块整理系统设置与认证能力。': 'System settings and authentication grouped by module.',
  基础设置: 'Base Settings',
  'AI 模型': 'AI Models',
  数据源: 'Data Sources',
  通知渠道: 'Notifications',
  'Agent 设置': 'Agent Settings',
  回测配置: 'Backtest Settings',
  智能导入: 'Smart Import',
  当前分类配置项: 'Current Category Settings',
  自选股列表: 'Watchlist',
};

const PARTIAL_TRANSLATIONS: Array<[RegExp, string]> = [
  [/共\s*(\d+)\s*项/g, '$1 items'],
  [/第\s*(\d+)\s*\/\s*(\d+)\s*页/g, 'Page $1 / $2'],
  [/市场：A\s*股（cn）/g, 'Market: China A-shares (cn)'],
  [/市场：港股（hk）/g, 'Market: Hong Kong (hk)'],
  [/市场：美股（us）/g, 'Market: US (us)'],
  [/全部账户/g, 'All Accounts'],
  [/账户视图/g, 'Account View'],
  [/成本口径/g, 'Cost Method'],
  [/先进先出（FIFO）/g, 'FIFO'],
  [/均价成本（AVG）/g, 'Average Cost'],
  [/全部买卖方向/g, 'All Sides'],
  [/全部资金方向/g, 'All Cash Directions'],
  [/全部公司行为/g, 'All Corporate Actions'],
  [/券商 CSV 导入/g, 'Broker CSV Import'],
  [/仅预演（不写入）/g, 'Dry run only'],
  [/系统设置/g, 'Settings'],
  [/版本信息/g, 'Version Info'],
  [/配置备份/g, 'Config Backup'],
];

function translateText(value: string): string {
  if (!HAN_RE.test(value)) return value;
  const trimmed = value.trim();
  if (!trimmed) return value;

  let translated = EXACT_TRANSLATIONS[trimmed] || value;
  for (const [pattern, replacement] of PARTIAL_TRANSLATIONS) {
    translated = translated.replace(pattern, replacement);
  }
  if (HAN_RE.test(translated)) {
    return value.replace(/[\u3400-\u9fff][\u3400-\u9fff，。；：、“”（）《》？！\sA-Za-z0-9/%._-]*/g, (match) => {
      const exact = EXACT_TRANSLATIONS[match.trim()];
      return exact || 'Legacy non-English text hidden';
    });
  }
  return translated;
}

function shouldSkipElement(element: Element | null): boolean {
  if (!element) return false;
  const tag = element.tagName.toLowerCase();
  return tag === 'script' || tag === 'style' || tag === 'noscript';
}

function sanitizeNode(root: Node): void {
  if (root.nodeType === Node.TEXT_NODE) {
    const value = root.textContent || '';
    if (HAN_RE.test(value) && root.parentElement && !shouldSkipElement(root.parentElement)) {
      root.textContent = translateText(value);
    }
    return;
  }

  if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) {
    return;
  }

  const element = root.nodeType === Node.ELEMENT_NODE ? root as Element : null;
  if (shouldSkipElement(element)) return;

  if (element instanceof HTMLElement) {
    for (const attr of ['aria-label', 'title', 'placeholder']) {
      const value = element.getAttribute(attr);
      if (value && HAN_RE.test(value)) {
        element.setAttribute(attr, translateText(value));
      }
    }
  }

  root.childNodes.forEach(sanitizeNode);
}

export function installEnglishOnlyDomGuard(): () => void {
  if (typeof window === 'undefined' || typeof MutationObserver === 'undefined') {
    return () => undefined;
  }

  let scheduled = false;
  const run = () => {
    scheduled = false;
    sanitizeNode(document.body);
  };
  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(run);
  };

  schedule();
  const observer = new MutationObserver(schedule);
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ['aria-label', 'title', 'placeholder'],
  });

  return () => observer.disconnect();
}
