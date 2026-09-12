"""Thin Playwright adapter for the Kasra web application.

The gateway rejects plain HTTP clients at login (docs/kasra-contract.md, section 2), so every
read and write happens inside a real Chrome session driven by Playwright. This module only
knows *how* to open forms and read or set page controls; which document to create and when is
decided elsewhere (``kasra_reconcile``). Writes are read-only by default: a call is a dry run
unless ``confirm`` is passed explicitly, and no credential, cookie or session value is ever
printed, logged or returned.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit

from .kasra_reconcile import JALALI_MONTHS
from .normalize import normalize

DEFAULT_STATE = 'var/kasra-recon/session-state.json'
DEFAULT_CHROME = '/usr/bin/google-chrome'
MENU_API = '/Lego.Web/Menu/GetAllUserMenu'
DAILY_REPORT_MENU = 1302
DOCUMENTS_MENU = 13157
DAILY_REPORT_FRAME = 'MainDailyReport'
DOCUMENTS_FRAME = 'DocInfoNew'
DAILY_REPORT_GRID = 'ctl00_ContentPlaceHolder1_GrdDailyReport'
DOCUMENTS_PERIOD_SELECT = 'ctl00_ContentPlaceHolder1_CmbWorkPeriod'
DOCUMENTS_GRID_CLASS = 'k-selectable'
LAUNCH_ARGS = ['--no-sandbox', '--disable-blink-features=AutomationControlled']

# Observed tab metadata, used only when the shell menu cannot be fetched.
MENU_FALLBACK = {
    DAILY_REPORT_MENU: {'Id': DAILY_REPORT_MENU, 'url': '/TAPresentation/App_Pages/Reports/MainDailyReport',
                        'Title': 'كاركرد روزانه', 'NewMenu': 0, 'IsReAuth': 0, 'SubSystemTitle': None,
                        'HelpCode': None, 'urlPrefix': None},
    DOCUMENTS_MENU: {'Id': DOCUMENTS_MENU, 'url': '/TAPresentation/App_Pages/Reports/DocInfoNew',
                     'Title': 'نمايش مجوزها', 'NewMenu': 0, 'IsReAuth': 0, 'SubSystemTitle': None,
                     'HelpCode': None, 'urlPrefix': None},
}


class KasraError(RuntimeError):
    """Kasra could not be read or written as requested."""


class KasraAuthError(KasraError):
    """The saved session is gone and no usable credentials are available."""


def save_session_state(context, path):
    """Persist the authenticated session and keep it owner-only.

    The session file carries live cookies, so its mode is tightened explicitly instead of
    relying on the process umask or on the parent directory mode alone.
    """
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    os.chmod(path, 0o600)
    return path


def work_period_option(options, label):
    """Return the work-period value whose text matches a Jalali month label."""
    wanted = normalize(label)
    for value, text in options or ():
        if normalize(text) == wanted:
            return value
    return None


def _grid_script(grid_id, class_name):
    return """(args) => {
        let table = args.id ? document.getElementById(args.id) : null;
        if (!table && args.cls) {
            for (const candidate of document.querySelectorAll('table')) {
                const rows = Array.from(candidate.rows || []);
                if ((candidate.className || '').includes(args.cls) && rows.length > 1
                    && rows.some(row => (row.cells || []).length >= 20)) { table = candidate; break; }
            }
        }
        if (!table) return { columns: [], rows: [] };
        const rows = Array.from(table.rows || []);
        const header = rows.length && rows[0].cells && rows[0].cells.length
            && /TH/i.test((rows[0].cells[0].tagName || '')) ? rows.shift() : null;
        let columns = header ? Array.from(header.cells).map(cell => (cell.innerText || '').trim()) : [];
        if (!columns.length && args.cls) {
            for (const candidate of document.querySelectorAll('table')) {
                const headerCells = Array.from(candidate.querySelectorAll('th'));
                if (headerCells.length === 30 || headerCells.length === 32) {
                    columns = headerCells.map(cell => (cell.innerText || '').trim());
                    break;
                }
            }
        }
        const clean = (cell) => ((cell.innerText || '').trim().replace(/\\s+/g, ' '));
        return { columns: columns,
                 rows: rows.map(row => Array.from(row.cells || []).map(clean)).filter(row => row.some(Boolean)) };
    }"""


class KasraBrowser:
    """A thin, dry-run-by-default Kasra session."""

    def __init__(self, base_url, *, state_path=DEFAULT_STATE, username=None, password=None,
                 headless=True, executable_path=DEFAULT_CHROME, timeout_ms=60000, locale='fa-IR',
                 timezone_id='Asia/Tehran'):
        parts = urlsplit(base_url or '')
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment):
            raise ValueError('KASRA_URL must be an HTTPS base URL without credentials, query or fragment')
        self.base_url = base_url.rstrip('/')
        self.state_path = Path(state_path)
        self.username = username or None
        self._password = password or None
        self.headless = headless
        self.executable_path = executable_path
        self.timeout_ms = timeout_ms
        self.locale = locale
        self.timezone_id = timezone_id
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._menu = None

    # --- lifecycle --------------------------------------------------------

    def open(self):
        """Launch Chrome, restore the saved session and reach the authenticated shell."""
        if self.page is not None:
            return self.page
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(executable_path=self.executable_path,
                                                       headless=self.headless, args=list(LAUNCH_ARGS))
        options = {'locale': self.locale, 'timezone_id': self.timezone_id,
                   'viewport': {'width': 1800, 'height': 1000}}
        if self.state_path.exists():
            options['storage_state'] = str(self.state_path)
        self.context = self.browser.new_context(**options)
        self.page = self.context.new_page()
        self.page.goto(self.base_url + '/', wait_until='domcontentloaded', timeout=self.timeout_ms)
        self.page.wait_for_timeout(6000)
        if self.page.locator('#UserName').count():
            self._login()
        self.page.wait_for_function("() => typeof LayoutNameSpace !== 'undefined'", timeout=self.timeout_ms)
        return self.page

    def close(self):
        for closer in (getattr(self.browser, 'close', None), getattr(self.playwright, 'stop', None)):
            try:
                if closer:
                    closer()
            except Exception:
                pass
        self.page = self.browser = self.context = self.playwright = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exception):
        self.close()

    def _login(self):
        if not (self.username and self._password):
            raise KasraAuthError('Saved Kasra session is no longer valid and no credentials are available')
        self.page.fill('#UserName', self.username)
        self.page.fill('#Password', self._password)
        self.page.click('#btnSubmit')
        self.page.wait_for_load_state('domcontentloaded', timeout=self.timeout_ms)
        self.page.wait_for_timeout(4000)
        if self.page.locator('#UserName').count():
            raise KasraAuthError('Kasra login was rejected')
        if self.context is not None:
            save_session_state(self.context, self.state_path)

    def _require_page(self):
        if self.page is None:
            raise KasraError('Kasra browser session is not open')
        return self.page

    # --- shell navigation -------------------------------------------------

    def menu(self):
        """The account's menu, fetched through the authenticated page when possible."""
        if self._menu is not None:
            return self._menu
        rows = self._require_page().evaluate(
            """async (path) => {
                try {
                    const response = await fetch(path, {credentials: 'same-origin',
                        headers: {'X-Requested-With': 'XMLHttpRequest'}});
                    if (!response.ok) return null;
                    const data = await response.json();
                    return Array.isArray(data) && data.length ? data : null;
                } catch (error) { return null; }
            }""", MENU_API)
        self._menu = rows if rows else list(MENU_FALLBACK.values())
        return self._menu

    def open_menu(self, menu_id):
        """Open a menu form inside the shell as its own tab iframe."""
        entry = next((row for row in self.menu() if row.get('Id') == menu_id), None)
        if entry is None:
            raise KasraError(f'Menu id {menu_id} is not available for this account')
        self._require_page().evaluate(
            """(entry) => { LayoutNameSpace.AddNewTab(entry.Id, entry.FullUrl || entry.url, entry.Title, "",
                entry.NewMenu, entry.IsReAuth === 1, entry.SubSystemTitle, entry.HelpCode, entry.urlPrefix); }""",
            entry)
        self.page.wait_for_timeout(9000)
        return entry

    def frame_for(self, fragment, timeout_ms=None):
        """Return the loaded iframe whose URL contains a fragment."""
        page = self._require_page()
        deadline = (timeout_ms or 30000) // 1000
        for _ in range(deadline):
            for frame in page.frames:
                if fragment in frame.url:
                    return frame
            page.wait_for_timeout(1000)
        raise KasraError(f'Kasra frame {fragment} did not load')

    def _grid(self, frame, grid_id=None, class_name=None):
        return frame.evaluate(_grid_script(grid_id, class_name), {'id': grid_id, 'cls': class_name})

    # --- reads ------------------------------------------------------------

    def read_daily_report(self, start, end):
        """Read the daily work report table for a Jalali range (read-only)."""
        self.open_menu(DAILY_REPORT_MENU)
        frame = self.frame_for(DAILY_REPORT_FRAME)
        frame.evaluate("""(range) => {
            for (const id of ['TxtSDate', 'SDate', 'ctl00_ContentPlaceHolder1_SDate']) {
                const el = document.getElementById(id);
                if (el) { el.value = range[0]; el.dispatchEvent(new Event('change', {bubbles: true})); }
            }
            for (const id of ['TxtEDate', 'EDate', 'ctl00_ContentPlaceHolder1_EDate']) {
                const el = document.getElementById(id);
                if (el) { el.value = range[1]; el.dispatchEvent(new Event('change', {bubbles: true})); }
            }
        }""", [start, end])
        frame.click('#BtnFilter')
        self.page.wait_for_timeout(9000)
        return self._grid(frame, DAILY_REPORT_GRID)

    def read_documents(self, start, end):
        """Read the document inquiry for every Jalali month spanning the range (read-only)."""
        self.open_menu(DOCUMENTS_MENU)
        frame = self.frame_for(DOCUMENTS_FRAME)
        columns = []
        rows = []
        seen = set()
        for label in self._month_labels(start, end):
            options = frame.evaluate("""(id) => {
                const el = document.getElementById(id);
                return el ? Array.from(el.options).map(option => [option.value, (option.text || '').trim()]) : [];
            }""", DOCUMENTS_PERIOD_SELECT)
            value = work_period_option(options, label)
            if value is None:
                raise KasraError('Kasra does not offer the requested work period')
            frame.evaluate("""(args) => {
                const el = document.getElementById(args.id);
                if (el) { el.value = args.value; el.dispatchEvent(new Event('change', {bubbles: true})); }
            }""", {'id': DOCUMENTS_PERIOD_SELECT, 'value': value})
            frame.click('#OToolBar_BtnFilter')
            self.page.wait_for_timeout(9000)
            table = self._grid(frame, None, DOCUMENTS_GRID_CLASS)
            columns = columns or table['columns']
            for row in table['rows']:
                key = tuple(row)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
        return {'columns': columns, 'rows': rows}

    def _month_labels(self, start, end):
        months = []
        year, month = int(start[:4]), int(start[5:7])
        end_year, end_month = int(end[:4]), int(end[5:7])
        while (year, month) <= (end_year, end_month):
            months.append(f'{JALALI_MONTHS[month]} {year}')
            month += 1
            if month == 13:
                year, month = year + 1, 1
        return months

    def work_period_value(self, start, end, options):
        """First period value needed for a Jalali range (useful for diagnostics)."""
        labels = self._month_labels(start, end)
        return work_period_option(options, labels[0]) if labels else None

    def read_person_id(self):
        """The person id of the logged-in account, read from the open form (read-only)."""
        if self.page is None:
            raise KasraError('Kasra browser session is not open')
        try:
            frame = self.frame_for(DOCUMENTS_FRAME, timeout_ms=5000)
        except KasraError:
            self.open_menu(DOCUMENTS_MENU)
            frame = self.frame_for(DOCUMENTS_FRAME)
        return frame.evaluate("""() => {
            const direct = document.getElementById('ctl00_ContentPlaceHolder1_CmbPerson_txtOnLineUserID');
            if (direct && direct.value) return direct.value;
            const select = Array.from(document.querySelectorAll('select'))
                .find(node => /PersonAutoComplete/.test(node.id));
            return (select && select.value) || null;
        }""")

    # --- writes (never performed unless confirm is set) --------------------

    def write_credit_document(self, payload, *, confirm=False):
        """Create one credit document. Without ``confirm`` nothing is opened or sent."""
        if not confirm:
            return {'mode': 'dry_run', 'written': False, 'document_id': None, 'status_id': None,
                    'payload': payload}
        self._require_page()
        self.open_menu(DOCUMENTS_MENU)
        frame = self.frame_for(DOCUMENTS_FRAME)
        frame.evaluate("""() => { const el = document.getElementById('BtnRegisterCredit');
            OnClickBtnRegisterCredit(el, {preventDefault(){}, stopPropagation(){}, currentTarget: el, target: el}); }""")
        self.page.wait_for_timeout(12000)
        prefix = self.page.evaluate("""() => {
            const field = Array.from(document.querySelectorAll('input,textarea'))
                .find(node => /EnterCreditNameSpace_Description$/.test(node.id));
            return field ? field.id.replace(/Description$/, '') : null;
        }""")
        if not prefix:
            raise KasraError('Kasra credit request form did not open')
        values = {f'{prefix}CreditType': str(payload['credit_type']), f'{prefix}CreditGroup': '1',
                  f'{prefix}StartDate': payload['start_date'], f'{prefix}DayCount': payload.get('day_count', '1'),
                  f'{prefix}StartTime': payload['start_time'], f'{prefix}EndDate': payload['end_date'],
                  f'{prefix}EndTime': payload['end_time'], f'{prefix}Description': payload['description']}
        self.page.evaluate("""(values) => {
            for (const [id, value] of Object.entries(values)) {
                const el = document.getElementById(id);
                if (!el) continue;
                el.value = value;
                for (const type of ['input', 'change', 'keyup', 'blur']) {
                    el.dispatchEvent(new Event(type, {bubbles: true}));
                }
                if (el.tagName === 'SELECT') { el.dispatchEvent(new Event('change', {bubbles: true})); }
            }
        }""", values)
        self.page.click(f'#{prefix}BtnSave')
        self.page.wait_for_timeout(12000)
        return {'mode': 'confirmed', 'written': True, 'document_id': None, 'status_id': None,
                'payload': payload, 'note': 'read back the document list to record the document id'}

    def delete_document(self, doc_id, *, confirm=False):
        """Delete exactly one document id. Without ``confirm`` nothing is clicked."""
        if not confirm:
            return {'mode': 'dry_run', 'deleted': False, 'doc_id': doc_id}
        self._require_page()
        self.open_menu(DOCUMENTS_MENU)
        frame = self.frame_for(DOCUMENTS_FRAME)
        selected = frame.evaluate("""(docId) => {
            for (const table of document.querySelectorAll('table')) {
                for (const row of Array.from(table.rows || [])) {
                    const cells = Array.from(row.cells || []).map(cell => (cell.innerText || '').trim());
                    if (!cells.includes(docId)) continue;
                    const box = row.querySelector('input[type=checkbox]');
                    if (box) { box.click(); return true; }
                }
            }
            return false;
        }""", str(doc_id))
        if not selected:
            raise KasraError('Document id was not found in the document list')
        frame.click('#OToolBar_BtnDel')
        self.page.wait_for_timeout(3000)
        for button in ('ctl00_ContentPlaceHolder1_BtnOk', 'BtnOk'):
            ok = frame.evaluate("(id) => { const el = document.getElementById(id); return !!el; }", button)
            if ok:
                frame.click(f'#{button}')
                break
        self.page.wait_for_timeout(5000)
        return {'mode': 'confirmed', 'deleted': True, 'doc_id': str(doc_id)}
