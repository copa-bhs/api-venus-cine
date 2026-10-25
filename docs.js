import { state } from '../state.js';

export async function render(container) {
    const base = location.origin;
    
    container.innerHTML = `
        <div class="animate-fade-in-up">
            <div class="mb-8">
                <h2 class="text-2xl font-black">Documentação da API</h2>
                <p class="text-xs text-gray-400 mt-1">Aprenda a integrar o Vênus API no seu sistema de forma simples e segura.</p>
            </div>

            <div class="card p-6 border-yellow-500/30 mb-8 bg-yellow-500/5 relative overflow-hidden">
                <div class="absolute -right-4 -top-4 w-24 h-24 bg-yellow-500 rounded-full blur-[40px] opacity-10"></div>
                <h3 class="font-bold text-yellow-400 mb-2 flex items-center gap-2"><i class="fa-solid fa-triangle-exclamation"></i> AVISO IMPORTANTE: Como economizar requisições</h3>
                <p class="text-sm text-gray-300 mb-3">
                    Cada requisição feita à nossa API consome 1 crédito do seu plano. Se o seu aplicativo fizer chamadas diretas (ex: abrir a tela de filmes), você vai gastar rapidamente.
                </p>
                <p class="text-sm text-gray-300">
                    <b>O segredo dos grandes apps:</b> Tenha o seu próprio servidor/banco de dados (Firebase, SQLite). Faça uma requisição para o Vênus API <b>1 ou 2 vezes por dia</b>, salve a lista no seu servidor, e mostre para os seus usuários a partir do <b>seu</b> banco. Assim, você usa apenas ~2 requisições por dia e tem milhares de usuários!
                </p>
            </div>

            <div class="card p-6 border-green-500/30 mb-8 bg-green-500/5">
                <h3 class="font-bold text-green-400 mb-2 flex items-center gap-2"><i class="fa-solid fa-key"></i> Cole sua API Key aqui</h3>
                <p class="text-xs text-gray-400 mb-3">Para gerar os exemplos corretos abaixo, cole a chave completa que você criou.</p>
                <input id="doc-api-key" type="text" class="input-dark w-full font-mono text-sm mb-2" placeholder="vn_live_..." oninput="atualizarLinksDoc()">
            </div>

            <!-- FILMES -->
            <div class="card p-6 mb-6">
                <h3 class="font-bold text-white mb-2 flex items-center gap-2"><i class="fa-solid fa-film text-red-500"></i> Listar Filmes</h3>
                <p class="text-xs text-gray-400 mb-4">Retorna uma lista paginada com todos os filmes disponíveis.</p>
                
                <div class="text-[10px] font-bold text-gray-500 mb-1 uppercase">Requisição (GET)</div>
                <div class="flex items-center gap-2 bg-[#0a0a0a] border border-[#222] p-3 rounded-xl mb-4">
                    <code class="text-xs font-mono text-green-400 flex-1 overflow-x-auto">${base}/v1/filmes?page=1&size=30&api_key=<span id="key-filmes">SUA_CHAVE</span></code>
                    <button onclick="copiarReq('${base}/v1/filmes?page=1&size=30&api_key=', 'key-filmes')" class="text-gray-500 hover:text-white px-2"><i class="fa-regular fa-copy"></i></button>
                </div>

                <div class="text-[10px] font-bold text-gray-500 mb-1 uppercase">Retorno (JSON)</div>
                <div class="code-block text-gray-300">
{
  "page": 1, "page_size": 30, "total_items": 15044,
  "items": [
    {
      "id": "2127", "title": "A ODISSEIA", "poster": "https://...", 
      "logo": "https://...", "type": "movie", "year": 2026,
      "slug": "a-odisseia-2026", "classificacao": "16",
      "generos": ["Ação", "Aventura"], "score": 8.5
    }
  ]
}
                </div>
            </div>
            
            <!-- SÉRIES -->
            <div class="card p-6 mb-6">
                <h3 class="font-bold text-white mb-2 flex items-center gap-2"><i class="fa-solid fa-tv text-purple-500"></i> Listar Séries</h3>
                <p class="text-xs text-gray-400 mb-4">Mesma estrutura dos filmes, mas para séries.</p>
                <div class="flex items-center gap-2 bg-[#0a0a0a] border border-[#222] p-3 rounded-xl">
                    <code class="text-xs font-mono text-green-400 flex-1 overflow-x-auto">${base}/v1/series?page=1&api_key=<span id="key-series">SUA_CHAVE</span></code>
                    <button onclick="copiarReq('${base}/v1/series?page=1&api_key=', 'key-series')" class="text-gray-500 hover:text-white px-2"><i class="fa-regular fa-copy"></i></button>
                </div>
            </div>
            
            <!-- INFO FILME -->
            <div class="card p-6 mb-6">
                <h3 class="font-bold text-white mb-2 flex items-center gap-2"><i class="fa-solid fa-circle-info text-red-500"></i> Detalhes de um Filme</h3>
                <div class="flex items-center gap-2 bg-[#0a0a0a] border border-[#222] p-3 rounded-xl mb-4">
                    <code class="text-xs font-mono text-green-400 flex-1 overflow-x-auto">${base}/v1/info/filme/2127?api_key=<span id="key-info-filme">SUA_CHAVE</span></code>
                    <button onclick="copiarReq('${base}/v1/info/filme/2127?api_key=', 'key-info-filme')" class="text-gray-500 hover:text-white px-2"><i class="fa-regular fa-copy"></i></button>
                </div>
                <div class="code-block text-gray-300">
{
  "id": "2127", "titulo": "A ODISSEIA", "sinopse": "...",
  "logo": "https://...", "classificacao": "16",
  "elenco": [{"nome": "Tom Holland", "personagem": "Odisseu"}],
  "url_stream": "${base}/v1/stream/a-odisseia-2026.mp4?api_key=SUA_CHAVE"
}
                </div>
            </div>
            
            <!-- INFO SÉRIE -->
            <div class="card p-6 mb-6">
                <h3 class="font-bold text-white mb-2 flex items-center gap-2"><i class="fa-solid fa-circle-info text-purple-500"></i> Detalhes de uma Série</h3>
                <div class="flex items-center gap-2 bg-[#0a0a0a] border border-[#222] p-3 rounded-xl mb-4">
                    <code class="text-xs font-mono text-green-400 flex-1 overflow-x-auto">${base}/v1/info/serie/4521?api_key=<span id="key-info-serie">SUA_CHAVE</span></code>
                    <button onclick="copiarReq('${base}/v1/info/serie/4521?api_key=', 'key-info-serie')" class="text-gray-500 hover:text-white px-2"><i class="fa-regular fa-copy"></i></button>
                </div>
                <div class="code-block text-gray-300">
{
  "id": "4521", "titulo": "Breaking Bad", "sinopse": "...",
  "logo": "https://...", "classificacao": "16",
  "elenco": [{"nome": "Bryan Cranston", "personagem": "Walter White"}],
  "temporadas": [{"numero": 1, "episodios": [...]}]
}
                </div>
            </div>
        </div>
    `;

    window.atualizarLinksDoc = function() {
        const key = document.getElementById('doc-api-key').value.trim() || 'SUA_CHAVE';
        const spans = ['key-filmes', 'key-series', 'key-info-filme', 'key-info-serie'];
        spans.forEach(id => { const el = document.getElementById(id); if(el) el.textContent = key; });
    };

    window.copiarReq = function(baseUrl, spanId) {
        const el = document.getElementById(spanId);
        const key = el ? el.textContent : 'SUA_CHAVE';
        window.copy(baseUrl + key);
    };
}