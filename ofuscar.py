import re
import subprocess
import os

# Lê o HTML original
with open("painel.html", "r", encoding="utf-8") as f:
    html = f.read()

# Encontra o bloco de script principal
pattern = re.compile(r'(<script>)(.*?)(</script>)', re.DOTALL)
match = pattern.search(html)

if not match:
    print("Nenhum script encontrado.")
else:
    script_original = match.group(2)
    
    # Salva o script temporariamente
    with open("temp_script.js", "w", encoding="utf-8") as f:
        f.write(script_original)
    
    # Roda o ofuscador
    print("Ofuscando código JavaScript...")
    subprocess.run(["javascript-obfuscator", "temp_script.js", "--output", "temp_script_ofuscado.js", "--compact", "true", "--control-flow-flattening", "true", "--dead-code-injection", "true", "--string-array", "true", "--string-array-encoding", "base64"])
    
    # Lê o script ofuscado
    with open("temp_script_ofuscado.js", "r", encoding="utf-8") as f:
        script_ofuscado = f.read()
    
    # Substitui no HTML original
    novo_html = html[:match.start(2)] + script_ofuscado + html[match.end(2):]
    
    # Salva o novo HTML
    with open("painel.html", "w", encoding="utf-8") as f:
        f.write(novo_html)
    
    # Limpa os arquivos temporários
    os.remove("temp_script.js")
    os.remove("temp_script_ofuscado.js")
    
    print("✅ Ofuscação concluída! O HTML foi atualizado.")
