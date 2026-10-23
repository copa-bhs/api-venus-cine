import { Splash } from './components/splash.js';
// import { PullToRefresh } from './components/pull-refresh.js'; // ⚡ REMOVIDO
import { CommentsSheet } from './components/comments-sheet.js';
import { Fab } from './components/fab.js';
import { Router } from './router.js';
import { SearchView } from './views/search.js';
import { LoginView } from './views/login.js';
import { ProfileSelectView } from './views/profile-select.js';
import { ProfileCreateView } from './views/profile-create.js';
import { StorageService } from './services/storage.js';
import { CommentsService } from './services/comments.js';
import { AuthService } from './services/auth.js';
import { FirestoreService } from './services/firestore.js';
import { ProfileService } from './services/profile.js';
import { DevicesService } from './services/devices.js';
import { I18n } from './services/i18n.js';
import { TokenService } from './services/tokens.js';

window._APP_VER = 'lock-v23';
console.log('🚀 Vênus Cine - versão ' + window._APP_VER);

window.StorageServiceRef = StorageService;
window.CommentsServiceRef = CommentsService;
window.SearchView = SearchView;
window.AuthServiceRef = AuthService;
window.FirestoreServiceRef = FirestoreService;
window.LoginViewRef = LoginView;
window.ProfileServiceRef = ProfileService;
window.DevicesServiceRef = DevicesService;
window.I18nRef = I18n;
window.TokenServiceRef = TokenService;

let ProfileManageView = null;
async function loadProfileManageView() {
    if (ProfileManageView) return ProfileManageView;
    try {
        const m = await import('./views/profile-manage.js');
        ProfileManageView = m.ProfileManageView;
        return ProfileManageView;
    } catch(e) { return null; }
}
window._loadProfileManageView = loadProfileManageView;

// TOAST
let _toastTimer = null;
window.showToast = function(title) {
    let t = document.getElementById('venus-toast');
    if (!t) { t = document.createElement('div'); t.id = 'venus-toast'; document.body.appendChild(t); }
    t.textContent = title || '';
    t.classList.remove('visible');
    void t.offsetWidth;
    t.classList.add('visible');
    if (_toastTimer) clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => t.classList.remove('visible'), 2200);
};

// CONFIRM
window.showConfirm = function(message, onConfirm, options = {}) {
    const title = options.title || 'Confirmação';
    const confirmText = options.confirmText || 'OK';
    const cancelText = options.cancelText || 'Cancelar';
    const isDanger = options.danger === true;
    document.getElementById('venus-confirm')?.remove();
    const modal = document.createElement('div');
    modal.id = 'venus-confirm';
    modal.className = 'vc-confirm-overlay';
    modal.innerHTML = `<div class="vc-confirm-box"><div class="vc-confirm-title">${title}</div><div class="vc-confirm-message">${message}</div><div class="vc-confirm-actions"><button class="vc-confirm-btn vc-confirm-cancel" id="vcConfirmCancel">${cancelText}</button><button class="vc-confirm-btn vc-confirm-ok ${isDanger ? 'vc-confirm-danger' : ''}" id="vcConfirmOk">${confirmText}</button></div></div>`;
    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add('visible'));
    const close = () => { modal.classList.remove('visible'); setTimeout(() => modal.remove(), 250); };
    document.getElementById('vcConfirmCancel')?.addEventListener('click', () => close());
    document.getElementById('vcConfirmOk')?.addEventListener('click', () => { close(); setTimeout(() => { if (typeof onConfirm === 'function') onConfirm(); }, 200); });
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
};

let _appStarted = false;

function lockUI() {
    ['app-container', 'bottom-nav', 'fab-comment', 'fab-filter', 'main-header'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
}

function unlockUI() {
    const c = document.getElementById('app-container');
    const n = document.getElementById('bottom-nav');
    const h = document.getElementById('main-header');
    if (c) c.style.display = '';
    if (n) n.style.display = '';
    if (h) h.style.display = '';
}

function startApp() {
    if (_appStarted) return;
    _appStarted = true;
    if (typeof window._cancelSplashWatchdog === 'function') window._cancelSplashWatchdog();
    document.body.classList.add('app-ready');
    unlockUI();
    try { Router.init(); } catch(e) { console.error('[Vênus] Router.init:', e); }
    updateHeaderProfile();
}

function updateHeaderProfile() {
    const btn = document.getElementById('headerProfileBtn');
    if (!btn) return;
    const profile = window._activeProfile;
    const user = AuthService.getUser();
    const foto = (profile && profile.fotoPerfil) || (user && user.photoURL) || '';
    if (foto) {
        btn.innerHTML = '<img src="' + foto + '" alt="Perfil" onerror="this.style.display=\'none\';this.parentElement.innerHTML=\'<i class=&quot;fa-solid fa-user&quot; style=&quot;font-size:17px;color:#E50914;&quot;></i>\';">';
    } else {
        btn.innerHTML = '<i class="fa-solid fa-user" style="color:#E50914;"></i>';
    }
    btn.title = (profile && profile.nome) || (user && (user.displayName || user.email)) || 'Perfil';
}
window.updateHeaderProfile = updateHeaderProfile;

async function checkProfileAndProceed() {
    const user = AuthService.getUser();
    if (!user) { startApp(); return; }

    const savedTokenUid = localStorage.getItem('venus_token_uid');
    if (savedTokenUid && !window._activeUid) {
        window._activeUid = savedTokenUid;
        window._isTokenLogin = true;
    }

    window._activeUid = window._activeUid || user.uid;

    if (!window._isTokenLogin) {
        try { await TokenService.getOrCreateToken(user.uid); } catch(e) {}
    }

    StorageService.setUser({ uid: window._activeUid });

    let profile = null;
    try { profile = await ProfileService.get(window._activeUid); }
    catch(e) { console.error('[Vênus] Erro ao buscar perfil:', e); }

    if (!profile) {
        ProfileCreateView.show({
            onSuccess: () => setTimeout(() => startApp(), 100)
        });
        return;
    }

    ProfileSelectView.show(profile, {
        onSuccess: (p) => {
            ProfileSelectView.close();
            window._activeProfile = p;
            setTimeout(() => startApp(), 100);
        }
    });
}

function hideSplashAndProceed() {
    if (typeof window._cancelSplashWatchdog === 'function') window._cancelSplashWatchdog();
    const sp = document.getElementById('splash-screen');
    if (sp && !sp.classList.contains('fade-out')) {
        sp.classList.add('fade-out');
        setTimeout(() => { try { sp.remove(); } catch(e) {} }, 600);
    }
    let logged = false;
    try { logged = AuthService.isLoggedIn ? AuthService.isLoggedIn() : false; } catch(e) {}
    if (logged) { checkProfileAndProceed(); return; }
    try {
        LoginView.show({ skipable: false, onSuccess: () => checkProfileAndProceed() });
    } catch(e) {
        startApp();
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    try { I18n.init(); } catch(e) {}
    try { lockUI(); } catch(e) {}
    
    try {
        CommentsSheet.initDrag();
        CommentsSheet.bindEvents();
        Fab.init();
        // PullToRefresh.init(); // ⚡ REMOVIDO
    } catch(e) {}

    try {
        document.getElementById('headerSearchBtn')?.addEventListener('click', () => SearchView.open());
        document.getElementById('headerProfileBtn')?.addEventListener('click', async () => {
            const profile = window._activeProfile;
            if (!profile) { window.showToast('Perfil não carregado'); return; }
            const PMV = await loadProfileManageView();
            if (!PMV) { window.showToast('Tela de perfil indisponível'); return; }
            PMV.show(profile, {
                onSaved: (updated) => { window._activeProfile = updated; updateHeaderProfile(); },
                onLogout: () => location.reload()
            });
        });
    } catch(e) {}

    try {
        let ticking = false;
        window.addEventListener('scroll', () => {
            if (!ticking) {
                window.requestAnimationFrame(() => {
                    const d = document.getElementById('scroll-darkener');
                    if (d) d.style.opacity = Math.min(1, window.scrollY / 300);
                    const h = document.getElementById('main-header');
                    if (h) h.classList.toggle('scrolled', window.scrollY > 20);
                    ticking = false;
                });
                ticking = true;
            }
        }, { passive: true });
    } catch(e) {}

    try {
        if (AuthService && typeof AuthService.init === 'function') {
            const u = await AuthService.init();
            StorageService.setUser(u);
        }
    } catch(e) {}

    hideSplashAndProceed();

    try {
        if (AuthService && typeof AuthService.onChange === 'function') {
            AuthService.onChange(async (user) => {
                if (!window._isTokenLogin) {
                    window._activeUid = user ? user.uid : null;
                }

                const prevUser = StorageService.getUser();
                const prevUid = prevUser ? prevUser.uid : null;
                const newUid = user ? user.uid : null;

                if (prevUid !== newUid && !window._isTokenLogin) {
                    StorageService.clearLocalCache();
                    CommentsService.clearLikesCache?.();
                }

                if (window._activeUid) {
                    StorageService.setUser({ uid: window._activeUid });
                } else {
                    StorageService.setUser(user);
                }

                if (user && window._activeUid) {
                    try {
                        await DevicesService.register(window._activeUid);
                        if (window._deviceHeartbeat) clearInterval(window._deviceHeartbeat);
                        window._deviceHeartbeat = setInterval(() => {
                            DevicesService.heartbeat(window._activeUid);
                        }, 2 * 60 * 1000);
                    } catch(e) {}

                    try {
                        const cloud = await FirestoreService.getWatchlist(window._activeUid);
                        StorageService.setWatchlistDirect(cloud);
                    } catch(e) {}

                    try { CommentsService.syncLikesFromFirestore(); } catch(e) {}
                } else {
                    if (window._deviceHeartbeat) {
                        clearInterval(window._deviceHeartbeat);
                        window._deviceHeartbeat = null;
                    }
                }

                if (typeof window.updateHeaderAuth === 'function') window.updateHeaderAuth(user);
                if (_appStarted && Router.currentTab === 'watchlist') Router.navigate('watchlist');
            });
        }
    } catch(e) {}
});

function updateHeaderAuth(user) {
    const btn = document.getElementById('headerAuthBtn');
    if (!btn) return;
    if (user) {
        btn.innerHTML = user.photoURL
            ? '<img src="' + user.photoURL + '" style="width:100%;height:100%;border-radius:50%;object-fit:cover;" onerror="this.style.display=\'none\';this.parentElement.innerHTML=\'<i class=&quot;fa-solid fa-user&quot; style=&quot;font-size:13px;color:#E50914;&quot;></i>\';">'
            : '<i class="fa-solid fa-user" style="font-size:13px;color:#E50914;"></i>';
        btn.style.padding = '2px';
        btn.title = user.displayName || user.email || 'Minha conta';
    } else {
        btn.innerHTML = '<i class="fa-solid fa-user" style="font-size:14px;"></i>';
        btn.style.padding = '0';
        btn.title = 'Entrar';
    }
}

window.updateHeaderAuth = updateHeaderAuth;
window.startApp = startApp;
window.lockUI = lockUI;
window.unlockUI = unlockUI;