// Interface translation. It does not touch the model's replies: those come
// from the system prompt, which stays frozen and in English.
// Proper nouns (Cohesive LLM, Nextflow, Mermaid, the example pipeline names)
// are not translated.

const DICT = {
    en: {
        new_chat: 'New chat',
        delete_all: 'Delete all',
        recent: 'Recent',
        examples: 'Examples',
        reset: 'Reset',
        drawer: 'Drawer',
        chat: 'Chat',
        logout: 'Log out',
        ready: 'Ready',
        msg_placeholder: 'Message your Bioinformatics Pipeline Assistant...',
        welcome: "Welcome! I am your Bioinformatics Pipeline Assistant. Describe the pipeline you'd like to build, or choose one of the examples above.",
        generated_pipeline: 'Generated Pipeline',
        nextflow_code: 'Nextflow Code',
        mermaid_diagram: 'Mermaid Diagram',
        generated: 'Generated',
        code_placeholder: 'Generated code will appear here',
        validate: 'Validate',
        propose: 'Propose',
        validate_title: 'Validate with Nextflow',
        propose_title: 'Propose this pipeline as a Pull Request on the framework',
        components: 'Components',
        search_components: 'Search components...',
        saved_drawings: 'Saved drawings',
        new_drawing: 'New drawing',
        delete_all_drawings: 'Delete all saved drawings',
        generate_pipeline: 'Generate Pipeline',
        save: 'Save',
        export: 'Export',
        clear: 'Clear',
        nodes: 'nodes',
        hide: 'Hide',
        show_drawings: 'Show saved drawings',
        username: 'Username',
        password: 'Password',
        sign_in: 'Sign in',
        forgot_password: 'Forgot your password?',
        need_help: 'Need help?',
        language: 'Language',
    },
    it: {
        new_chat: 'Nuova chat',
        delete_all: 'Elimina tutto',
        recent: 'Recenti',
        examples: 'Esempi',
        reset: 'Reimposta',
        drawer: 'Editor grafico',
        chat: 'Chat',
        logout: 'Esci',
        ready: 'Pronto',
        msg_placeholder: 'Scrivi al tuo assistente per le pipeline bioinformatiche...',
        welcome: 'Benvenuto! Sono il tuo assistente per le pipeline bioinformatiche. Descrivi la pipeline che vuoi costruire, oppure scegli uno degli esempi qui sopra.',
        generated_pipeline: 'Pipeline generata',
        nextflow_code: 'Codice Nextflow',
        mermaid_diagram: 'Diagramma Mermaid',
        generated: 'Generato',
        code_placeholder: 'Qui comparirà il codice generato',
        validate: 'Valida',
        propose: 'Proponi',
        validate_title: 'Valida con Nextflow',
        propose_title: 'Proponi questa pipeline come Pull Request sul framework',
        components: 'Componenti',
        search_components: 'Cerca componenti...',
        saved_drawings: 'Disegni salvati',
        new_drawing: 'Nuovo disegno',
        delete_all_drawings: 'Elimina tutti i disegni salvati',
        generate_pipeline: 'Genera pipeline',
        save: 'Salva',
        export: 'Esporta',
        clear: 'Svuota',
        nodes: 'nodi',
        hide: 'Nascondi',
        show_drawings: 'Mostra i disegni salvati',
        username: 'Nome utente',
        password: 'Password',
        sign_in: 'Accedi',
        forgot_password: 'Password dimenticata?',
        need_help: 'Serve aiuto?',
        language: 'Lingua',
    },
    fr: {
        new_chat: 'Nouvelle discussion',
        delete_all: 'Tout supprimer',
        recent: 'Récents',
        examples: 'Exemples',
        reset: 'Réinitialiser',
        drawer: 'Éditeur graphique',
        chat: 'Discussion',
        logout: 'Se déconnecter',
        ready: 'Prêt',
        msg_placeholder: 'Écrivez à votre assistant pour les pipelines bio-informatiques...',
        welcome: "Bienvenue ! Je suis votre assistant pour les pipelines bio-informatiques. Décrivez le pipeline que vous souhaitez construire, ou choisissez l'un des exemples ci-dessus.",
        generated_pipeline: 'Pipeline généré',
        nextflow_code: 'Code Nextflow',
        mermaid_diagram: 'Diagramme Mermaid',
        generated: 'Généré',
        code_placeholder: 'Le code généré apparaîtra ici',
        validate: 'Valider',
        propose: 'Proposer',
        validate_title: 'Valider avec Nextflow',
        propose_title: 'Proposer ce pipeline comme Pull Request sur le framework',
        components: 'Composants',
        search_components: 'Rechercher des composants...',
        saved_drawings: 'Schémas enregistrés',
        new_drawing: 'Nouveau schéma',
        delete_all_drawings: 'Supprimer tous les schémas enregistrés',
        generate_pipeline: 'Générer le pipeline',
        save: 'Enregistrer',
        export: 'Exporter',
        clear: 'Effacer',
        nodes: 'nœuds',
        hide: 'Masquer',
        show_drawings: 'Afficher les schémas enregistrés',
        username: "Nom d'utilisateur",
        password: 'Mot de passe',
        sign_in: 'Se connecter',
        forgot_password: 'Mot de passe oublié ?',
        need_help: "Besoin d'aide ?",
        language: 'Langue',
    },
    es: {
        new_chat: 'Nuevo chat',
        delete_all: 'Eliminar todo',
        recent: 'Recientes',
        examples: 'Ejemplos',
        reset: 'Restablecer',
        drawer: 'Editor gráfico',
        chat: 'Chat',
        logout: 'Cerrar sesión',
        ready: 'Listo',
        msg_placeholder: 'Escribe a tu asistente de pipelines bioinformáticos...',
        welcome: '¡Bienvenido! Soy tu asistente de pipelines bioinformáticos. Describe el pipeline que quieres construir, o elige uno de los ejemplos de arriba.',
        generated_pipeline: 'Pipeline generado',
        nextflow_code: 'Código Nextflow',
        mermaid_diagram: 'Diagrama Mermaid',
        generated: 'Generado',
        code_placeholder: 'Aquí aparecerá el código generado',
        validate: 'Validar',
        propose: 'Proponer',
        validate_title: 'Validar con Nextflow',
        propose_title: 'Proponer este pipeline como Pull Request en el framework',
        components: 'Componentes',
        search_components: 'Buscar componentes...',
        saved_drawings: 'Diagramas guardados',
        new_drawing: 'Nuevo diagrama',
        delete_all_drawings: 'Eliminar todos los diagramas guardados',
        generate_pipeline: 'Generar pipeline',
        save: 'Guardar',
        export: 'Exportar',
        clear: 'Vaciar',
        nodes: 'nodos',
        hide: 'Ocultar',
        show_drawings: 'Mostrar los diagramas guardados',
        username: 'Usuario',
        password: 'Contraseña',
        sign_in: 'Iniciar sesión',
        forgot_password: '¿Olvidaste tu contraseña?',
        need_help: '¿Necesitas ayuda?',
        language: 'Idioma',
    },
};


// No flags: a flag names a country, not a language. Spanish belongs to twenty
// of them, French to many more — and in Tunis the French flag would say
// something we do not intend. W3C i18n guidance: endonym (the language name
// written in that language) and a neutral icon.

export const LANGS = [
    ['en', 'English'],
    ['it', 'Italiano'],
    ['fr', 'Français'],
    ['es', 'Español'],
];

const STORAGE_KEY = 'izs_lang';

export function detectLang() {
    // navigator.languages is the ordered preference list the browser also
    // sends as Accept-Language: 'fr-CA' before 'en-GB' means French first.
    const wanted = (navigator.languages && navigator.languages.length)
        ? navigator.languages
        : [navigator.language || 'en'];
    for (const tag of wanted) {
        const base = String(tag).slice(0, 2).toLowerCase();
        if (DICT[base]) return base;
    }
    return 'en';
}

export function currentLang() {
    // an explicit choice always wins over detection
    let saved = null;
    try { saved = localStorage.getItem(STORAGE_KEY); } catch (e) {}
    if (saved && DICT[saved]) return saved;
    return detectLang();
}

export function t(key, lang = currentLang()) {
    return (DICT[lang] && DICT[lang][key]) || DICT.en[key] || key;
}

export function applyTranslations(root = document) {
    const lang = currentLang();
    document.documentElement.setAttribute('lang', lang);

    root.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        const target = el.querySelector('[data-i18n-slot]') || el;
        // replace the text node only: icons and inner badges survive
        const node = [...target.childNodes].find(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
        if (node) node.textContent = ' ' + t(key, lang) + ' ';
        else target.textContent = t(key, lang);
    });
    root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder'), lang));
    });
    root.querySelectorAll('[data-i18n-title]').forEach(el => {
        el.setAttribute('title', t(el.getAttribute('data-i18n-title'), lang));
    });
}

export function setLang(lang) {
    if (!DICT[lang]) return;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (e) {}
    applyTranslations();
    document.dispatchEvent(new CustomEvent('izs:langchange', { detail: { lang } }));
}

// Mounts into any header container.
// Hand-built because an <option> cannot be styled or carry markup.
export function mountLanguagePicker(container) {
    if (!container || container.querySelector('.lang-picker')) return;

    const wrap = document.createElement('div');
    wrap.className = 'lang-picker';

    const btn = document.createElement('button');
    btn.className = 'lang-current';
    btn.type = 'button';
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');

    const menu = document.createElement('div');
    menu.className = 'lang-menu';
    menu.setAttribute('role', 'listbox');

    function label(code, withCode = false) {
        const name = (LANGS.find(l => l[0] === code) || [, code])[1];
        const iso = withCode ? `<span class="lang-iso">${code.toUpperCase()}</span>` : '';
        return `<span class="lang-name">${name}</span>${iso}`;
    }

    function paint() {
        const cur = currentLang();
        btn.innerHTML = '<svg class="lang-globe" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M1.6 8h12.8M8 1.6c1.8 2 1.8 10.8 0 12.8M8 1.6c-1.8 2-1.8 10.8 0 12.8" fill="none" stroke="currentColor" stroke-width="1.1"/></svg>' + label(cur) + '<svg class="lang-chev" viewBox="0 0 12 12" width="9" height="9" aria-hidden="true"><path d="M2 4.5 6 8.5 10 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        btn.title = t('language');
        menu.querySelectorAll('.lang-option').forEach(o => {
            o.classList.toggle('active', o.dataset.lang === cur);
            o.setAttribute('aria-selected', o.dataset.lang === cur ? 'true' : 'false');
        });
    }

    for (const [code] of LANGS) {
        const o = document.createElement('button');
        o.type = 'button';
        o.className = 'lang-option';
        o.dataset.lang = code;
        o.setAttribute('role', 'option');
        o.innerHTML = label(code, true) + '<svg class="lang-check" viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M2 6.3 4.8 9 10 3.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        o.addEventListener('click', () => {
            setLang(code);
            wrap.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
            paint();
        });
        menu.appendChild(o);
    }

    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = wrap.classList.toggle('open');
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', () => {
        wrap.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
    });
    document.addEventListener('izs:langchange', paint);

    wrap.appendChild(btn);
    wrap.appendChild(menu);
    // conventional order: tools, language, identity, session action.
    // Log out stays last on the right.
    const anchor = container.querySelector('.user-chip, #logoutBtn');
    container.insertBefore(wrap, anchor || null);
    paint();
    return wrap;
}
