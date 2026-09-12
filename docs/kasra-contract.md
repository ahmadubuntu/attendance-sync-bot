# Kasra contract — discovered from the live system (read-only)

All statements below were observed on 2026-09-12 against `kasra.charisma.ir` with only
read operations plus one interactive login. No registration, edit or delete was performed.
No secret, password, session cookie or personal record value is stored in this document.

## 1. Host and application layout

- Host: `kasra.charisma.ir` (HTTPS). HTTP requests are redirected to HTTPS.
- Shell application: `Lego.Web` (ASP.NET, IIS). The shell is entered at `/` which
  redirects to `/Lego.Web/Kevlar/Account/Login`.
- Feature applications live on the same host under their own roots and are embedded in
  the shell as tab iframes: `TAPresentation` (time and attendance forms and reports),
  `TA` (attendance display), `FrmPresentation`, `MyKasra`, `Rst`, `Desktop`, `Message`.
- Do not navigate to a feature root directly: the shell rewrites the path and the request
  fails (404/403). Forms are opened inside the shell, not as standalone pages.

## 2. Gateway behaviour that matters for automation

- A plain HTTP client receives `403 Forbidden` (or `401`) when posting the login form,
  even with a correct password payload and browser-like headers. The browser succeeds.
  Therefore authentication must be performed by a real Chromium instance where the
  site's own JavaScript builds the request.
- In the browser, always start from `https://kasra.charisma.ir/` (a cold request to
  `/Lego.Web/` can produce a doubled path and a 500 page). The login form is reached
  through the server redirect chain.
- Login page: title `ورود کاربران`, form action `/Lego.Web/Kevlar/Account/Login`,
  fields `UserName`, `Password`, plus hidden `CaptchaEnable`, `CountIncorrectPass`,
  `CountIncorrectPassWithCaptcha`, `originalCsrfToken`, `antiCsrfTokenValue__guid_*`,
  `keyTokenValue__guid_*`, `nonce`, `chkRequestData`, `passKey`, `passIv`.
- Password field value is `hashed_` + base64(AES-128-CBC, PKCS7) of the plain password
  with key and IV equal to the literal `8080808080808080` (from `MainSecurity.js`).
  This was verified byte-for-byte against the site's own CryptoJS output using Node as
  an oracle; it is **not** sufficient on its own because of the gateway behaviour above.
- Before the form is submitted, the page posts `{data: <json of the form fields except
  nonce/chkRequestData>}` to `/Lego.Web/api/Kevlar/RequestHeaderApi/GetHeader`, which
  returns `{nonce, checksum}`; both are then added to the form.
- After login, every XHR to an application path (`/kevlar/`, `/desktop/`, `/frm/`,
  `/lego/`, `/menu/`, `/message/`, `/mykasra/`, `/widget/`, `/scr/`, `/rst/`, `/ta/`)
  receives the same `nonce` and `chkRequestData` headers computed from the request query
  or body (`MainSecurity.js`, `SetHeader`). A client that omits them is rejected.
- Consequence: the Python client performs **no** authentication or anti-tamper signing.
  It drives Chromium with Playwright (system Chrome at `/usr/bin/google-chrome`) and
  keeps the authenticated context in a private session-state file. The site's own
  JavaScript produces every header.

## 3. Authenticated navigation

- After login the shell renders `سامانه های جامع کسرا` with a favourites menu and a
  workspace. Menu data comes from `GET /Lego.Web/Menu/GetAllUserMenu` as a JSON array of
  items with `Id`, `Title`, `ParentId`, `url`, `NewMenu`, `IsReAuth`, `HelpCode`.
- A tab is opened by calling, inside the shell page:
  `LayoutNameSpace.AddNewTab(id, url, title, "", newMenu, isReAuth, subSystemTitle, helpCode, urlPrefix)`.
- Observed menu ids used by this account:

| Menu id | Title | url |
|---|---|---|
| 1302 | كاركرد روزانه (daily work) | `/TAPresentation/App_Pages/Reports/MainDailyReport` |
| 1306 | كاركرد ماهانه (monthly work) | `/TAPresentation/App_Pages/Reports/MainMonthlyReport` |
| 13190 | نمايش ترددها (attendance) | `/TA/DisplayAttendance/DisplayAttendance?SystemID=131` |
| 13157 | نمايش مجوزها (documents) | `/TAPresentation/App_Pages/Reports/DocInfoNew` |
| 13152 | درخواست آيتم روزانه | `/TAPresentation/App_Pages/DataEntry/RequestDailyItem` |
| 131522 | مديريت مجوز كاركردي | `/TAPresentation/App_Pages/DataEntry/Admin/AdminCreditNew` |

## 4. Employee identity

- The shell resolves the logged-in person itself; the daily report and every form carry the
  person code and the shell user id in their own hidden fields. The adapter reads them from
  the page instead of storing them, so no personal identifier is kept in source or in this
  document.
- The person combo in every form displays the code and the employee name and can be
  switched only through the site's own picker.

## 5. Registered work — the daily report (menu 1302)

- The report frame is `MainDailyReport` with a Jalali range `TxtSDate` / `TxtEDate`
  (also mirrored to hidden `SDate`/`EDate`), a person combo, and filter button
  `#BtnFilter` (`OnClickBtnFilter` → `OnClickBtnSearch`).
- Column set (order observed): `رديف, تاريخ, روز, ترددها, حضور, مازاد حضور, اضافه كار,
  كسر حضور, تاخير, تعجيل, استحقاقي, استعلاجي, دوركاري, فعاليت ساعتي,
  دوركاري خارج از موظفي, شبكاري, آنكال, ماموريت ساعتي, ماموريت روزانه, ساختار, شيفت`.
- The two columns this project must fill are **`دوركاري`** and **`دوركاري خارج از موظفي`**.
- Work schedule for this person is `برنامه 7-16` (07:00–16:00) on Saturday–Tuesday and
  `07:00 الی 15:00` on Wednesday; Thursday and Friday are `استراحت`. This matches the
  quota rule already implemented (Saturday–Tuesday 540 min, Wednesday 480, Thursday and
  Friday zero).
- Day-type values seen in the `ترددها` column: `غيبت` (absence), `استراحت` (rest),
  `دوركاري خارج از موظفي`, and `دوركاري ساعتي (منتظر تایید)` for a request that is waiting
  for approval. A public holiday appears as its holiday title.
- Because the whole period is settled from the work schedule, `كسر حضور` (absence
  deficit) equals the full daily obligation whenever the regular part is not registered.

## 6. Document inquiry (menu 13157) and the credit-request modal

- The page lists the person's documents with `DocID`, `DocTypeID`, `StatusID`, requester,
  approver, request date, and the document's `RSDate`/`REDate` range.
- Observed `StatusID`: `201` = `در روند` (in progress, waiting for approval),
  `203` = `تایید شده` (approved). The status filter also offers `204` باطل شده,
  `205` حذف شده, `209` لغو شده.
- `DocTypeID`: `1` ساعتي, `2` روزانه, `3` تردد, `4` آيتم ماهانه, `5` آيتم روزانه,
  `7` مداومت كاري.
- Working periods are selectable by Jalali month (`CmbWorkPeriod`, e.g. `55` = شهريور 1405).
- Toolbar buttons: `درخواست مجوز` (`BtnRegisterCredit`), `درخواست تردد` (`BtnAttendance`),
  `درخواست آيتم روزانه` (`BtnDailyItem`), `فیلتر`, `حذف فیلتر`, `حذف`, `ویرایش`,
  PDF/Excel export.
- `درخواست مجوز` opens the modal `درخواست مجوز` (`EnterCreditNameSpace`) rendered by
  `LegoExtended/renderExtended` in the shell page.
- The modal populates two dropdown sources from the server:
  - `GET /Lego.Web/TA/EnterCredit/GetCreditKind` → `0` انتخاب نشده, `1` کسر حضور ساعتی,
    `2` کسر حضور روزانه, `3` مازاد حضور.
  - `GET /Lego.Web/TA/EnterCredit/GetCreditTitle` → the credit types available to this
    person: **`14085` دوركاري ساعتي**, `60054` **دوركاري خارج از موظفي**,
    `14090` ماموريت ساعتي, `11001` استحقاقي ساعتي.
- Modal fields (all under the `Modal_<pageId>_<instance>_EnterCreditNameSpace_` prefix):
  `PersonAutoComplete` (person), `CreditGroup`, `CreditType` (chosen with `btnSelectItem`
  → `EnterCreditNameSpace.onSelect`), `StartDate`, `DayCount`, `StartTime`, `EndDate`,
  `EndTime`, `Description`, plus `BtnSave` (ذخیره), `BtnSaveAndCreate`, `BtnConfirm`,
  and the member selector `BtnSelectMembers` (`InducteeComponentNameSpace.onAddInductee`).
- Dates are entered as `۱۴۰۵/۰۶/۲۱` (Jalali, Persian digits) and times as `HH:MM`.
- The modal pre-fills the current Jalali date and current clock.

## 7. What the account already has registered (observed, شهریور ۱۴۰۵)

- `مجوز دوركاري ساعتي` and `مجوز دوركاري خارج از موظفي` documents are created per day as
  type `ساعتي`, dated on the day they apply to.
- Overtime values already registered match the overtime computed from the chat log, for
  example `03:31` for 1405/06/09 (chat: 07:55–20:26), `02:05` for 06/10 (07:55–19:00),
  `02:35` for 06/14 (07:40–19:15), `02:20` for 06/15 (08:15–19:34). This independently
  confirms the extraction rules and the daily-quota model.
- Recent documents for 1405/06/17 and 1405/06/18 are still `در روند` and appear in the
  daily report as `دوركاري ساعتي (منتظر تایید)`.
- Days with no document show `غيبت` with the full `كسر حضور`; Thursday/Friday show
  `استراحت`; a public holiday is titled with its name.

## 8. Consequences for this project

1. Registration is a **request** that goes to an approver; the report must never describe a
   successful submission as finalized attendance. The correct wording is
   "request created" plus the returned document id and status.
2. The regular part is `دورکاری ساعتی` (id 14085) and the excess is
   `دورکاری خارج از موظفی` (id 60054); both are hourly documents with a start date/time
   and an end date/time plus a description.
3. Split overnight work therefore needs two documents (one per accounting day), which
   matches the already implemented midnight segmentation.
4. Reconciliation must compare against **documents** (type, date, status) rather than only
   the aggregated daily columns: a pending request is neither missing nor final.
5. Authentication and every request must run inside the real browser session; the Python
   HTTP client cannot authenticate on its own.

## 9. Still unverified (must be settled before the first write)

- The exact AJAX endpoint and payload of `EnterCreditNameSpace.onClickBtnSave`, the
  duplicate-detection behaviour of the server, and the response shape (document id and
  status). These can only be observed by submitting once, which requires explicit
  per-change approval.
- Whether a document can cover multiple days in one submission (`DayCount` > 1) and how
  the approver workflow behaves for a retroactive date.
- Whether `دورکاری ساعتی` must also be requested for days that are fully covered by a
  schedule, to clear `كسر حضور`.
