// Traduzione dell'interfaccia. Non tocca le risposte del modello: quelle
// arrivano dal prompt di sistema, che resta congelato e in inglese.
// Nomi propri (Cohesive LLM, Nextflow, Mermaid, i nomi delle pipeline di
// esempio) non si traducono.

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
        language: 'Idioma',
    },
};


// Niente bandiere: rappresentano paesi, non lingue. Lo spagnolo e' di venti
// paesi, il francese di molti piu' di uno — e a Tunisi la bandiera francese
// direbbe qualcosa che non intendiamo dire. Raccomandazione W3C i18n:
// endonimo (il nome della lingua nella lingua stessa) e icona neutra.

export const LANGS = [
    ['en', 'English'],
    ['it', 'Italiano'],
    ['fr', 'Français'],
    ['es', 'Español'],
];

const STORAGE_KEY = 'izs_lang';

export function currentLang() {
    let saved = null;
    try { saved = localStorage.getItem(STORAGE_KEY); } catch (e) {}
    if (saved && DICT[saved]) return saved;
    // si parte dalla lingua del browser, se e' fra quelle coperte
    const nav = (navigator.language || 'en').slice(0, 2).toLowerCase();
    return DICT[nav] ? nav : 'en';
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
        // si sostituisce solo il testo: icone e pastiglie interne restano
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

// Selettore da agganciare a un contenitore qualsiasi dell'intestazione.
// E' costruito a mano perche' un <option> non puo' contenere un'immagine.
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
        btn.innerHTML = '<i class="fas fa-globe lang-globe"></i>' + label(cur)
                      + '<i class="fas fa-chevron-down lang-chev"></i>';
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
        o.innerHTML = label(code, true) + '<i class="fas fa-check lang-check"></i>';
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
    // ordine convenzionale: strumenti, lingua, identita', uscita.
    // L'azione di sessione resta l'ultima a destra.
    const anchor = container.querySelector('.user-chip, #logoutBtn');
    container.insertBefore(wrap, anchor || null);
    paint();
    return wrap;
}
