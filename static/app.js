(() => {
  const app = document.querySelector("#app");
  const toast = document.querySelector("#toast");
  const state = {
    csrf: "", user: null, data: null, page: "home", collapsed: false, query: "",
    journals: null, journalsLoading: false, journalsError: "",
    trialBalance: null, trialBalanceLoading: false, trialBalanceError: "",
  };
  const roleNames = { admin: "مدير", accountant: "محاسب", viewer: "قارئ" };
  const natureNames = { debit: "مدينة", credit: "دائنة" };
  let toastTimer;

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(url, {
        ...options,
        signal: controller.signal,
        headers: {
          ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
          ...(options.headers || {}),
        },
      });
      let payload = {};
      try { payload = await response.json(); } catch { /* The server returned a non-JSON error. */ }
      if (response.status === 401 && url !== "/api/login") {
        state.user = null;
        state.data = null;
        renderLogin("انتهت جلستك. سجّل الدخول للمتابعة.");
        throw new Error("انتهت الجلسة");
      }
      if (!response.ok) throw new Error(payload.error || "تعذر إكمال الطلب. حاول مرة أخرى.");
      return payload;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("استغرق الاتصال وقتًا طويلًا. تحقق من اتصالك ثم أعد المحاولة.");
      if (error instanceof TypeError) throw new Error("تعذر الاتصال بالخدمة. تحقق من اتصالك ثم أعد المحاولة.");
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  function showToast(message, kind = "success") {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.className = `toast visible ${kind}`;
    toastTimer = setTimeout(() => { toast.className = "toast"; }, 4500);
  }

  function renderLogin(error = "") {
    app.innerHTML = `
      <section class="login-layout">
        <div class="login-card">
          <div class="brand-mark">ر</div>
          <p class="eyebrow">نظام مالي للمنشآت</p>
          <h1>رصــــيـــد</h1>
          <p class="muted">سجّل الدخول إلى مساحة منشأتك التجريبية.</p>
          ${error ? `<div class="alert error" role="alert">${escapeHtml(error)}</div>` : ""}
          <form id="login-form" class="stack">
            <label>البريد الإلكتروني<input name="email" type="email" autocomplete="username" required value="admin@raseed.local"></label>
            <label>كلمة المرور<input name="password" type="password" autocomplete="current-password" required autofocus></label>
            <button class="button primary wide" type="submit">تسجيل الدخول</button>
          </form>
          <p class="hint">بيانات الدخول الأولية تظهر مرة واحدة في الطرفية عند تشغيل الخادم لأول مرة.</p>
        </div>
        <aside class="login-aside"><span class="aside-orbit"></span><p>وضوح أكبر في حسابات منشأتك.</p><span>نسخة أولية للتجربة والتطوير</span></aside>
      </section>`;
    document.querySelector("#login-form").addEventListener("submit", login);
  }

  async function login(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    button.disabled = true;
    button.textContent = "جارٍ تسجيل الدخول…";
    try {
      const result = await request("/api/login", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      state.csrf = result.csrf_token;
      state.user = result.user;
      await loadWorkspace();
    } catch (error) {
      renderLogin(error.message);
    }
  }

  async function loadWorkspace() {
    try {
      state.data = await request("/api/bootstrap");
      render();
    } catch (error) {
      if (state.user) {
        app.innerHTML = `<section class="error-screen"><div class="error-symbol">!</div><h1>تعذر تحميل بيانات المنشأة</h1><p>${escapeHtml(error.message)}</p><button id="retry" class="button primary">إعادة المحاولة</button><button id="signout" class="button ghost">تسجيل الخروج</button></section>`;
        document.querySelector("#retry").onclick = loadWorkspace;
        document.querySelector("#signout").onclick = logout;
      }
    }
  }

  async function logout() {
    try {
      await request("/api/logout", { method: "POST", headers: { "X-CSRF-Token": state.csrf }, body: "{}" });
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      state.user = null;
      state.data = null;
      state.csrf = "";
      renderLogin();
    }
  }

  function render() {
    if (!state.data) return;
    const company = state.data.company;
    const isAdmin = state.user.role === "admin";
    const links = [
      ["home", "◫", "نظرة عامة"],
      ["accounts", "▤", "دليل الحسابات"],
      ["journal", "↔", "القيود اليومية"],
      ["trial-balance", "▥", "ميزان المراجعة"],
      ["import", "⇧", "استيراد الدليل"],
      ...(isAdmin ? [["company", "⌂", "المنشأة"], ["users", "♙", "المستخدمون"]] : []),
    ];
    app.innerHTML = `
      <div class="workspace ${state.collapsed ? "sidebar-collapsed" : ""}">
        <aside class="sidebar">
          <div class="sidebar-brand"><span class="brand-mark small">ر</span><span class="brand-word">رصــــيـــد</span></div>
          <div class="company-switch"><span class="company-avatar">${escapeHtml(company.name.slice(0, 1))}</span><span class="company-name">${escapeHtml(company.name)}<small>المنشأة الحالية</small></span></div>
          <nav aria-label="القائمة الرئيسية">
            <p class="nav-caption">مساحة العمل</p>
            ${links.map(([page, icon, label]) => `<button class="nav-link ${state.page === page ? "active" : ""}" data-page="${page}"><span class="nav-icon">${icon}</span><span class="nav-label">${label}</span></button>`).join("")}
          </nav>
          <div class="sidebar-bottom"><div class="user-chip"><span class="user-avatar">${escapeHtml(state.user.name.slice(0, 1))}</span><span class="nav-label">${escapeHtml(state.user.name)}<small>${roleNames[state.user.role] || "مستخدم"}</small></span></div><button id="logout" class="nav-link logout-link"><span class="nav-icon">↪</span><span class="nav-label">تسجيل الخروج</span></button></div>
        </aside>
        <section class="main-column">
          <header class="topbar">
            <button id="toggle-nav" class="icon-button" aria-label="طي القائمة">☰</button>
            <div class="quick-search"><span>⌕</span><input id="quick-search" placeholder="ابحث عن حساب بالاسم أو الرمز…" value="${escapeHtml(state.query)}" aria-label="بحث سريع في دليل الحسابات"><kbd>⌘ K</kbd></div>
            <div class="topbar-actions"><span class="status-pill"><i></i> مساحة تجريبية</span><span class="topbar-avatar">${escapeHtml(state.user.name.slice(0, 1))}</span></div>
          </header>
          <main class="content" id="page-content">${pageContent()}</main>
          <footer class="footer"><span>رصــــيـــد · نسخة أولية للتجربة والتطوير</span><span>لا تمثل هذه النسخة إقرارًا بالتوافق المحاسبي أو التنظيمي</span></footer>
        </section>
      </div>`;
    bindWorkspace();
  }

  function pageContent() {
    if (state.page === "accounts") return accountsPage();
    if (state.page === "journal") return journalsPage();
    if (state.page === "trial-balance") return trialBalancePage();
    if (state.page === "import") return importPage();
    if (state.page === "company") return companyPage();
    if (state.page === "users") return usersPage();
    return homePage();
  }

  function pageHeading(eyebrow, title, description, action = "") {
    return `<div class="page-heading"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p class="muted">${description}</p></div>${action}</div>`;
  }

  function homePage() {
    const active = state.data.accounts.filter((account) => account.active).length;
    const groups = state.data.accounts.filter((account) => account.is_group).length;
    const audits = state.data.audits.length
      ? `<div class="activity-list">${state.data.audits.map((item) => `<div class="activity-row"><span class="activity-dot"></span><span><b>${auditLabel(item.action)}</b><small>${escapeHtml(formatDate(item.created_at))}</small></span></div>`).join("")}</div>`
      : `<div class="empty-state compact"><span class="empty-icon">◷</span><b>لا توجد عمليات مسجلة بعد</b><span>ستظهر هنا التغييرات الحساسة على المنشأة والحسابات.</span></div>`;
    return `
      ${pageHeading("مساحة العمل", "أهلًا بك في رصيد", "أدر دليل الحسابات، وسجل القيود اليومية المتوازنة، وراجع ميزان المراجعة.",
        `<span class="updated-label">آخر تحديث: ${escapeHtml(formatDate(state.data.updated_at))}</span>`)}
      <section class="metric-grid">
        <article class="metric-card"><span class="metric-icon green">▤</span><span class="metric-label">حسابات الدليل</span><strong>${state.data.accounts.length}</strong><small>عدد الحسابات المسجلة</small></article>
        <article class="metric-card"><span class="metric-icon gold">⌂</span><span class="metric-label">حسابات رئيسية</span><strong>${groups}</strong><small>المجموعات الهرمية</small></article>
        <article class="metric-card"><span class="metric-icon blue">✓</span><span class="metric-label">حسابات نشطة</span><strong>${active}</strong><small>حسابات متاحة للاستخدام</small></article>
      </section>
      <div class="home-grid">
        <section class="panel"><div class="panel-heading"><div><h2>ابدأ إعداد المنشأة</h2><p>أضف دليل الحسابات أو استورده بعد مراجعته.</p></div><span class="step-badge">المرحلة ١</span></div>
          <div class="setup-steps"><button data-page="company"><span>١</span><b>راجع بيانات المنشأة</b><small>اسم المنشأة وعملة العرض</small><i>←</i></button><button data-page="accounts"><span>٢</span><b>أضف الحسابات</b><small>رموز وأسماء وأنواع واضحة</small><i>←</i></button><button data-page="journal"><span>٣</span><b>سجل قيدًا يوميًا</b><small>مدين ودائن متساويان</small><i>←</i></button></div>
        </section>
        <section class="panel"><div class="panel-heading"><div><h2>سجل النشاط</h2><p>آخر تغييرات مسجلة في المنشأة</p></div><span class="subtle-count">${state.data.audits.length}</span></div>${audits}</section>
      </div>
      <div class="notice info"><span>ⓘ</span><p>القيود اليدوية المتوازنة وميزان المراجعة متاحان. القيود الآلية والمخزون والضرائب تحتاج إعدادات وسياسات منفصلة.</p></div>`;
  }

  function amount(minor) {
    const formatted = new Intl.NumberFormat("ar-SA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      .format(minor / 100);
    return `${formatted} ${escapeHtml(state.data.company.currency)}`;
  }

  function journalsPage() {
    const heading = pageHeading("المحاسبة", "القيود اليومية", "سجل القيود المتوازنة على حسابات الحركة. القيود المحفوظة لا يمكن تعديلها أو حذفها.");
    if (state.journalsError) {
      return `${heading}<div class="alert error">${escapeHtml(state.journalsError)}</div><button id="retry-journals" class="button secondary">إعادة المحاولة</button>`;
    }
    if (state.journalsLoading || !state.journals) {
      return `${heading}<div class="panel"><div class="empty-state compact">جارٍ تحميل القيود…</div></div>`;
    }
    const postingAccounts = state.data.accounts.filter((item) => item.active && !item.is_group);
    const accountOptions = postingAccounts.map((item) =>
      `<option value="${escapeHtml(item.code)}">${escapeHtml(item.code)} — ${escapeHtml(item.name)}</option>`).join("");
    const rows = state.journals.map((entry) => {
      const lines = entry.lines.map((line) => `<tr><td class="code-cell">${escapeHtml(line.code)}</td>
        <td>${escapeHtml(line.name)}</td><td>${escapeHtml(line.description || "—")}</td>
        <td>${line.debit_minor ? amount(line.debit_minor) : "—"}</td>
        <td>${line.credit_minor ? amount(line.credit_minor) : "—"}</td></tr>`).join("");
      return `<tr><td class="code-cell">${escapeHtml(entry.entry_number)}</td><td>${escapeHtml(entry.date)}</td>
        <td>${escapeHtml(entry.description)}</td><td>${amount(entry.total_minor)}</td>
        <td><details class="journal-details"><summary>عرض السطور (${entry.lines.length})</summary>
          <div class="table-wrap"><table><thead><tr><th>الحساب</th><th>الاسم</th><th>البيان</th><th>مدين</th><th>دائن</th></tr></thead><tbody>${lines}</tbody></table></div>
        </details></td></tr>`;
    }).join("");
    const form = state.user.role !== "viewer" ? `
      <section class="panel form-panel"><div class="panel-heading"><div><h2>قيد يومي جديد</h2>
        <p>لن يُحفظ القيد إلا إذا تساوى إجمالي المدين والدائن.</p></div></div>
        ${postingAccounts.length < 2 ? `<div class="notice warning"><span>!</span><p>أضف حسابي حركة نشطين على الأقل إلى دليل الحسابات قبل تسجيل قيد.</p></div>` : ""}
        <form id="journal-form" class="stack">
          <div class="form-grid journal-meta">
            <label>تاريخ القيد<input name="date" type="date" required value="${new Date().toISOString().slice(0, 10)}"></label>
            <label class="journal-description">وصف القيد<input name="description" maxlength="500" required placeholder="سبب العملية أو مرجعها"></label>
          </div>
          <div class="journal-lines-heading"><b>سطور القيد</b><button id="add-journal-line" class="button secondary" type="button">إضافة سطر</button></div>
          <div id="journal-lines">${journalLine(accountOptions)}${journalLine(accountOptions)}</div>
          <div class="button-row"><button class="button primary" type="submit" ${postingAccounts.length < 2 ? "disabled" : ""}>حفظ وترحيل القيد</button></div>
        </form>
      </section>` : "";
    return `${heading}${form}
      <section class="panel table-panel"><div class="panel-heading"><div><h2>القيود المسجلة</h2>
        <p>أحدث 100 قيد، مرتبة من الأحدث.</p></div><span class="subtle-count">${state.journals.length}</span></div>
        ${state.journals.length ? `<div class="table-wrap"><table><thead><tr><th>رقم القيد</th><th>التاريخ</th><th>البيان</th><th>الإجمالي</th><th>التفاصيل</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : `<div class="empty-state"><span class="empty-icon">↔</span><b>لا توجد قيود مسجلة</b><span>ابدأ بتسجيل أول قيد يومي متوازن.</span></div>`}
      </section>`;
  }

  function journalLine(accountOptions) {
    return `<div class="journal-line"><label>حساب الحركة<select class="line-account" required><option value="">اختر الحساب</option>${accountOptions}</select></label>
      <label>الجانب<select class="line-side"><option value="debit">مدين</option><option value="credit">دائن</option></select></label>
      <label>المبلغ<input class="line-amount" type="number" min="0.01" step="0.01" required placeholder="0.00" dir="ltr"></label>
      <label>بيان السطر<input class="line-description" maxlength="250" placeholder="اختياري"></label>
      <button class="icon-button remove-journal-line" type="button" aria-label="حذف السطر">×</button></div>`;
  }

  function trialBalancePage() {
    const heading = pageHeading("التقارير", "ميزان المراجعة", "أرصدة الحسابات النشطة من القيود المرحلة. يجب أن يتساوى إجمالي الأرصدة المدينة والدائنة.");
    if (state.trialBalanceError) {
      return `${heading}<div class="alert error">${escapeHtml(state.trialBalanceError)}</div><button id="retry-trial-balance" class="button secondary">إعادة المحاولة</button>`;
    }
    if (state.trialBalanceLoading || !state.trialBalance) {
      return `${heading}<div class="panel"><div class="empty-state compact">جارٍ إعداد التقرير…</div></div>`;
    }
    const report = state.trialBalance;
    const rows = report.accounts.map((account) => `<tr><td class="code-cell">${escapeHtml(account.code)}</td>
      <td>${escapeHtml(account.name)}</td><td>${escapeHtml(account.type_label)}</td>
      <td>${amount(account.debit_minor)}</td><td>${amount(account.credit_minor)}</td>
      <td>${amount(account.debit_balance_minor)}</td><td>${amount(account.credit_balance_minor)}</td></tr>`).join("");
    return `${heading}<section class="panel table-panel">
      <div class="panel-heading"><div><h2>الأرصدة حتى تاريخه</h2><p>يعرض الحسابات ذات الحركة فقط.</p></div>
        <span class="state-badge ${report.balanced ? "enabled" : "disabled"}">${report.balanced ? "متوازن" : "غير متوازن"}</span></div>
      ${report.accounts.length ? `<div class="table-wrap"><table><thead><tr><th>الرمز</th><th>الحساب</th><th>النوع</th><th>إجمالي مدين</th><th>إجمالي دائن</th><th>رصيد مدين</th><th>رصيد دائن</th></tr></thead>
        <tbody>${rows}</tbody><tfoot><tr><th colspan="5">إجمالي الأرصدة</th><th>${amount(report.total_debit_minor)}</th><th>${amount(report.total_credit_minor)}</th></tr></tfoot></table></div>`
        : `<div class="empty-state"><span class="empty-icon">▥</span><b>لا توجد حركات محاسبية</b><span>سجّل قيدًا يوميًا لعرض الأرصدة هنا.</span></div>`}
      </section>`;
  }

  function accountsPage() {
    const query = state.query.trim().toLowerCase();
    const accounts = state.data.accounts.filter((item) => !query || item.code.toLowerCase().includes(query)
      || item.name.toLowerCase().includes(query) || (item.english_name || "").toLowerCase().includes(query));
    const rows = accounts.map((item) => `<tr><td class="code-cell">${escapeHtml(item.code)}</td><td><span class="${item.parent_id ? "child-account" : ""}">${escapeHtml(item.name)}</span>${item.english_name ? `<small class="secondary-text">${escapeHtml(item.english_name)}</small>` : ""}</td><td><span class="type-badge">${escapeHtml(item.type_label)}</span></td><td>${natureNames[item.nature]}</td><td>${item.is_group ? "رئيسي" : "فرعي"}</td><td><span class="state-badge ${item.active ? "enabled" : "disabled"}">${item.active ? "نشط" : "غير نشط"}</span></td></tr>`).join("");
    const form = state.user.role !== "viewer" ? `<form id="account-form" class="form-grid">
      <label>رمز الحساب<input name="code" maxlength="64" required placeholder="مثال: 0010" dir="ltr"></label>
      <label>اسم الحساب<input name="name" maxlength="160" required placeholder="اسم واضح للحساب"></label>
      <label>نوع الحساب<select name="type" required>${Object.entries(state.data.types).map(([key, label]) => `<option value="${key}">${label}</option>`).join("")}</select></label>
      <label>طبيعة الحساب<select name="nature" required><option value="debit">مدينة</option><option value="credit">دائنة</option></select></label>
      <label>الحساب الأب<select name="parent_code"><option value="">بدون حساب أب</option>${state.data.accounts.filter((item) => item.is_group && item.active).map((item) => `<option value="${escapeHtml(item.code)}">${escapeHtml(item.code)} — ${escapeHtml(item.name)}</option>`).join("")}</select></label>
      <label class="check-label"><input type="checkbox" name="is_group"> حساب رئيسي (مجموعة)</label>
      <button class="button primary" type="submit">إضافة الحساب</button>
    </form>` : "";
    return `${pageHeading("المنشأة", "دليل الحسابات", "عرض وإدارة شجرة الحسابات. الحساب الرئيسي مجموعة ولا يقبل الترحيل لاحقًا.",
      state.user.role !== "viewer" ? `<button class="button secondary" data-page="import">استيراد من ملف <span>↑</span></button>` : "")}
      ${state.user.role !== "viewer" ? `<section class="panel form-panel"><div class="panel-heading"><div><h2>حساب جديد</h2><p>الرمز يُحفظ كنص كما أدخلته، بما في ذلك الأصفار في بدايته.</p></div></div>${form}</section>` : ""}
      <section class="panel table-panel"><div class="panel-heading"><div><h2>الحسابات</h2><p>تظهر أنواع الحسابات كلها بما فيها المصروفات وتكلفة المبيعات.</p></div><span class="subtle-count">${accounts.length} حساب</span></div>
        ${accounts.length ? `<div class="table-wrap"><table><thead><tr><th>الرمز</th><th>اسم الحساب</th><th>النوع</th><th>الطبيعة</th><th>المستوى</th><th>الحالة</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><span class="empty-icon">▤</span><b>${query ? "لا توجد نتائج مطابقة" : "دليل الحسابات فارغ"}</b><span>${query ? "جرّب البحث برمز أو اسم آخر." : "أضف حسابًا يدويًا أو استورد ملفًا بعد معاينته."}</span></div>`}
      </section>`;
  }

  function importPage() {
    return `${pageHeading("دليل الحسابات", "استيراد الحسابات", "ارفع ملف CSV أو Excel، راجع الفروقات والأخطاء، ثم أكد إضافة الحسابات الجديدة فقط.")}
      <div class="notice warning"><span>!</span><p>لن تُحذف الحسابات الحالية أو تُستبدل. الأنواع وطبيعة الحساب يجب أن تكون محددة في الملف؛ لا نخمنها من الاسم.</p></div>
      <section class="panel import-panel"><div class="panel-heading"><div><h2>ملف الحسابات</h2><p>الحد الأقصى 5 ميجابايت. الأعمدة المطلوبة: code, name, type, nature. يدعم الملف عدة أوراق.</p></div><span class="file-icon">⇧</span></div>
        <form id="import-form" class="upload-zone"><input id="import-file" type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required><label for="import-file"><span class="upload-symbol">↑</span><b id="file-name">اختر ملفًا أو اسحبه هنا</b><small>CSV أو XLSX — دون استبدال الحسابات الموجودة</small></label><button class="button primary" type="submit">معاينة الملف</button></form>
      </section><div id="import-result"></div>`;
  }

  function companyPage() {
    const canEdit = state.user.role === "admin";
    return `${pageHeading("الإعدادات", "بيانات المنشأة", "المعلومات الأساسية لمساحة العمل التجريبية.")}
      <section class="panel settings-panel"><div class="panel-heading"><div><h2>المنشأة الحالية</h2><p>بيانات المنشأة منفصلة عن الحسابات والمستخدمين الآخرين.</p></div><span class="company-avatar large">⌂</span></div>
      ${canEdit ? `<form id="company-form" class="stack settings-form"><label>اسم المنشأة<input name="name" maxlength="120" required value="${escapeHtml(state.data.company.name)}"></label><label>عملة العرض<input value="${escapeHtml(state.data.company.currency)}" disabled><small>تغيير العملات وإعداد الفترات المالية غير متاح في هذه المرحلة.</small></label><button class="button primary" type="submit">حفظ بيانات المنشأة</button></form>` : `<p>${escapeHtml(state.data.company.name)}</p>`}</section>
      <div class="notice info"><span>ⓘ</span><p>الفروع والمستودعات والفترات المالية ستضاف في مراحل لاحقة. لا توجد حاليًا قيود أو عمليات ترحيل.</p></div>`;
  }

  function usersPage() {
    const users = state.data.users || [];
    return `${pageHeading("الإعدادات", "المستخدمون والصلاحيات", "إدارة الوصول إلى هذه المنشأة فقط. تُراجع الصلاحيات في الخادم لكل عملية حساسة.")}
      <section class="panel form-panel"><div class="panel-heading"><div><h2>إضافة مستخدم</h2><p>كلمة المرور لا تُعرض بعد الحفظ؛ الحد الأدنى 12 حرفًا.</p></div></div>
      <form id="user-form" class="form-grid"><label>الاسم<input name="name" maxlength="100" required></label><label>البريد الإلكتروني<input name="email" type="email" required></label><label>كلمة المرور<input name="password" type="password" minlength="12" autocomplete="new-password" required></label><label>الدور<select name="role"><option value="accountant">محاسب — قراءة وتعديل دليل الحسابات</option><option value="viewer">قارئ — عرض فقط</option></select></label><button class="button primary" type="submit">إضافة المستخدم</button></form></section>
      <section class="panel table-panel"><div class="panel-heading"><div><h2>مستخدمو المنشأة</h2><p>إدارة المستخدمين وتغيير الأدوار متاح للمدير فقط.</p></div><span class="subtle-count">${users.length}</span></div>
      ${users.length ? `<div class="table-wrap"><table><thead><tr><th>الاسم</th><th>البريد الإلكتروني</th><th>الدور</th><th>الحالة</th></tr></thead><tbody>${users.map((user) => `<tr><td>${escapeHtml(user.name)}</td><td dir="ltr">${escapeHtml(user.email)}</td><td>${roleNames[user.role]}</td><td><span class="state-badge ${user.active ? "enabled" : "disabled"}">${user.active ? "نشط" : "غير نشط"}</span></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state"><b>لا يوجد مستخدمون</b></div>`}</section>
      <div class="notice warning"><span>!</span><p>إدارة الأدوار الحالية محدودة بالأدوار المعروضة. لا تتضمن هذه المرحلة سير اعتماد أو ترحيل محاسبي.</p></div>`;
  }

  function auditLabel(action) {
    return ({ login: "تسجيل دخول", logout: "تسجيل خروج", "company.updated": "تحديث بيانات المنشأة",
      "account.created": "إضافة حساب", "accounts.imported": "استيراد الحسابات", "user.created": "إضافة مستخدم" })[action] || "تغيير مسجل";
  }

  function formatDate(value) {
    try { return new Intl.DateTimeFormat("ar-SA", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
    catch { return "—"; }
  }

  function bindWorkspace() {
    document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => {
      state.page = button.dataset.page;
      render();
    }));
    document.querySelector("#toggle-nav").onclick = () => { state.collapsed = !state.collapsed; render(); };
    document.querySelector("#logout").onclick = logout;
    const search = document.querySelector("#quick-search");
    search.addEventListener("input", () => {
      state.query = search.value;
      if (state.page !== "accounts") state.page = "accounts";
      const content = document.querySelector("#page-content");
      content.innerHTML = accountsPage();
      bindAccountForm();
    });
    document.addEventListener("keydown", handleShortcut, { once: true });
    bindAccountForm();
    bindCompanyForm();
    bindUserForm();
    bindImportForm();
    bindJournalForm();
    if (state.page === "journal" && !state.journals && !state.journalsLoading && !state.journalsError) loadJournals();
    if (state.page === "trial-balance" && !state.trialBalance && !state.trialBalanceLoading
        && !state.trialBalanceError) loadTrialBalance();
    const retryJournals = document.querySelector("#retry-journals");
    if (retryJournals) retryJournals.onclick = () => { state.journalsError = ""; loadJournals(); };
    const retryTrialBalance = document.querySelector("#retry-trial-balance");
    if (retryTrialBalance) retryTrialBalance.onclick = () => { state.trialBalanceError = ""; loadTrialBalance(); };
    const file = document.querySelector("#import-file");
    if (file) file.addEventListener("change", () => {
      const label = document.querySelector("#file-name");
      if (label && file.files[0]) label.textContent = file.files[0].name;
    });
  }

  function handleShortcut(event) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      document.querySelector("#quick-search")?.focus();
    }
    document.addEventListener("keydown", handleShortcut, { once: true });
  }

  function bindAccountForm() {
    const form = document.querySelector("#account-form");
    if (!form) return;
    form.onsubmit = async (event) => {
      event.preventDefault();
      const fields = Object.fromEntries(new FormData(form));
      fields.is_group = form.elements.is_group.checked;
      fields.active = true;
      await submitForm(form, async () => request("/api/accounts", {
        method: "POST", headers: { "X-CSRF-Token": state.csrf }, body: JSON.stringify(fields),
      }), "تمت إضافة الحساب");
    };
  }

  function bindCompanyForm() {
    const form = document.querySelector("#company-form");
    if (form) form.onsubmit = async (event) => {
      event.preventDefault();
      await submitForm(form, async () => request("/api/company", {
        method: "POST", headers: { "X-CSRF-Token": state.csrf },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      }), "تم حفظ بيانات المنشأة");
    };
  }

  function bindUserForm() {
    const form = document.querySelector("#user-form");
    if (form) form.onsubmit = async (event) => {
      event.preventDefault();
      await submitForm(form, async () => request("/api/users", {
        method: "POST", headers: { "X-CSRF-Token": state.csrf },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      }), "تمت إضافة المستخدم");
    };
  }

  function bindJournalForm() {
    const form = document.querySelector("#journal-form");
    if (!form) return;
    document.querySelector("#add-journal-line").onclick = () => {
      const options = form.querySelector(".line-account").innerHTML;
      document.querySelector("#journal-lines").insertAdjacentHTML("beforeend", journalLine(options));
    };
    form.addEventListener("click", (event) => {
      if (event.target.closest(".remove-journal-line")) {
        event.target.closest(".journal-line").remove();
      }
    });
    form.onsubmit = async (event) => {
      event.preventDefault();
      const fields = Object.fromEntries(new FormData(form));
      fields.lines = Array.from(form.querySelectorAll(".journal-line"), (row) => {
        const amountValue = row.querySelector(".line-amount").value;
        const side = row.querySelector(".line-side").value;
        return {
          account_code: row.querySelector(".line-account").value,
          description: row.querySelector(".line-description").value,
          debit: side === "debit" ? amountValue : "0",
          credit: side === "credit" ? amountValue : "0",
        };
      });
      await submitForm(form, async () => request("/api/journals", {
        method: "POST", headers: { "X-CSRF-Token": state.csrf }, body: JSON.stringify(fields),
      }), "تم حفظ القيد وترحيله");
      state.journals = null;
      state.trialBalance = null;
    };
  }

  async function loadJournals() {
    state.journalsLoading = true;
    render();
    try {
      const result = await request("/api/journals");
      state.journals = result.journals;
    } catch (error) {
      state.journalsError = error.message;
    } finally {
      state.journalsLoading = false;
      if (state.page === "journal") render();
    }
  }

  async function loadTrialBalance() {
    state.trialBalanceLoading = true;
    render();
    try {
      state.trialBalance = await request("/api/reports/trial-balance");
    } catch (error) {
      state.trialBalanceError = error.message;
    } finally {
      state.trialBalanceLoading = false;
      if (state.page === "trial-balance") render();
    }
  }

  async function submitForm(form, action, success) {
    const button = form.querySelector('button[type="submit"]');
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "جارٍ الحفظ…";
    try {
      await action();
      showToast(success);
      await loadWorkspace();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      if (button.isConnected) { button.disabled = false; button.textContent = label; }
    }
  }

  function bindImportForm() {
    const form = document.querySelector("#import-form");
    if (!form) return;
    form.onsubmit = async (event) => {
      event.preventDefault();
      const file = document.querySelector("#import-file").files[0];
      if (!file) return;
      const button = form.querySelector("button");
      button.disabled = true;
      button.textContent = "جارٍ قراءة الملف…";
      try {
        const result = await request("/api/import/preview", {
          method: "POST", headers: { "X-CSRF-Token": state.csrf, "X-Filename": encodeURIComponent(file.name) }, body: file,
        });
        renderImportPreview(result);
      } catch (error) {
        document.querySelector("#import-result").innerHTML = `<div class="alert error">${escapeHtml(error.message)}</div>`;
      } finally {
        button.disabled = false;
        button.textContent = "معاينة الملف";
      }
    };
  }

  function renderImportPreview(result) {
    const issues = result.issues?.length
      ? `<div class="table-wrap issues-wrap"><table><thead><tr><th>الورقة</th><th>الصف</th><th>الرمز</th><th>سبب التجاوز</th></tr></thead><tbody>${result.issues.map((item) => `<tr><td>${escapeHtml(item.sheet || "—")}</td><td>${escapeHtml(item.row || "—")}</td><td>${escapeHtml(item.code || "—")}</td><td>${escapeHtml(item.reason)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="success-note">لم تُكتشف أخطاء في الصفوف.</div>`;
    document.querySelector("#import-result").innerHTML = `
      <section class="panel preview-panel"><div class="panel-heading"><div><h2>نتيجة المعاينة</h2><p>لم تُضف أي حسابات بعد. راجع الفروقات قبل التأكيد.</p></div><span class="preview-badge">معاينة فقط</span></div>
      <div class="preview-stats"><div><small>الأوراق</small><b>${result.sheets}</b></div><div><small>الأعمدة المقروءة</small><b>${result.columns}</b></div><div><small>الصفوف المقروءة</small><b>${result.read}</b></div><div><small>حسابات جديدة</small><b>${result.new}</b></div><div><small>مطابقة موجودة</small><b>${result.matched}</b></div><div><small>متخطاة / فاشلة</small><b class="${result.failed ? "text-danger" : ""}">${result.skipped} / ${result.failed}</b></div></div>
      ${issues}<div class="notice ${result.failed ? "warning" : "info"}"><span>!</span><p>${result.failed ? "لن يُعلن نجاح كامل؛ الحسابات السليمة فقط ستضاف إذا أكدت. راجع كل سبب تجاوز قبل المتابعة." : "سيتم إضافة الحسابات الجديدة فقط. الحسابات الموجودة لن تُحدّث أو تُحذف."}</p></div>
      <div class="button-row"><button id="confirm-import" class="button primary" ${result.new === 0 ? "disabled" : ""}>تأكيد إضافة ${result.new} حساب</button><button id="cancel-import" class="button secondary">إلغاء المعاينة</button></div></section>`;
    document.querySelector("#cancel-import").onclick = () => { document.querySelector("#import-result").innerHTML = ""; };
    document.querySelector("#confirm-import").onclick = async () => {
      if (!window.confirm(`تأكيد إضافة ${result.new} حساب جديد فقط؟ لن يتم استبدال أو حذف أي حساب موجود.`)) return;
      const button = document.querySelector("#confirm-import");
      button.disabled = true;
      button.textContent = "جارٍ الإضافة…";
      try {
        const outcome = await request("/api/import/commit", {
          method: "POST", headers: { "X-CSRF-Token": state.csrf }, body: JSON.stringify({ preview_id: result.preview_id }),
        });
        showToast(outcome.complete ? `اكتمل الاستيراد: ${outcome.created} حساب جديد` : `أضيف ${outcome.created} حساب، وتُرك ${outcome.skipped} صف للمراجعة`, outcome.complete ? "success" : "warning");
        state.page = "accounts";
        await loadWorkspace();
      } catch (error) {
        showToast(error.message, "error");
        button.disabled = false;
        button.textContent = "إعادة المحاولة";
      }
    };
  }

  async function start() {
    try {
      const result = await request("/api/session");
      if (!result.authenticated) { renderLogin(); return; }
      state.csrf = result.csrf_token;
      state.user = result.user;
      await loadWorkspace();
    } catch (error) {
      app.innerHTML = `<section class="error-screen"><div class="error-symbol">!</div><h1>تعذر الاتصال برصيد</h1><p>${escapeHtml(error.message)}</p><button class="button primary" id="retry-session">إعادة المحاولة</button></section>`;
      document.querySelector("#retry-session").onclick = start;
    }
  }

  start();
})();
