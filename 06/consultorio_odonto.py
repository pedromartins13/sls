import simpy
import random
import math
import statistics
 
# =====================================================
# PARÂMETROS GERAIS
# =====================================================
 
TEMPO_SIMULACAO    = 600
TEMPO_WARMUP       = 60          # minutos descartados no início
NUM_MAX_PACIENTES  = 50
INTERVALO_CHEGADA  = 15
 
PROB_PROFESSOR  = 0.3
PROB_RADIOLOGIA = 0.4
 
# Parâmetros triangulares: (mínimo, moda, máximo)
# Derivados de média ± desvio originais como aproximação razoável
TRI_RECEPCAO     = (0.5,  2.0,  4.0)
TRI_AVALIACAO    = (3.0,  5.0,  8.0)
TRI_HIGIENIZACAO = (3.0,  8.0, 16.0)
TRI_ATENDIMENTO  = (55.0, 90.0, 130.0)
TRI_PROFESSOR    = (8.0,  20.0, 35.0)
TRI_RADIOLOGIA   = (12.0, 25.0, 40.0)
TRI_FINALIZACAO  = (1.0,  5.0,  9.0)
 
LIMITE_DESISTENCIA = 7   # paciente desiste se fila >= este valor
MARGEM_FIM         = 30  # não inicia atendimento se restar < X min
 
# Dimensionamento de precisão
PRECISAO_DESEJADA = 10.0   # ± minutos desejados no IC 95%
T_CRITICO         = 2.009  # t-student 95%, ~50 graus de liberdade
 
NUM_REPLICACOES   = 50
 
 
# =====================================================
# DISTRIBUIÇÃO TRIANGULAR
# =====================================================
 
def triangular(a, c, b):
    """
    Amostra da distribuição triangular.
    a = mínimo, c = moda, b = máximo.
    Usa inversão analítica da CDF.
    """
    u = random.random()
    fc = (c - a) / (b - a)
    if u < fc:
        return a + math.sqrt(u * (b - a) * (c - a))
    else:
        return b - math.sqrt((1 - u) * (b - a) * (b - c))
 
 
# =====================================================
# DISTRIBUIÇÕES
# =====================================================
 
def distribuicoes(tipo):
    return max(0, {
        # Chegadas: exponencial (sem alteração)
        'chegada':     random.expovariate(1 / INTERVALO_CHEGADA),
 
        # Todas as demais: triangular
        'recepcao':    triangular(*TRI_RECEPCAO),
        'avaliacao':   triangular(*TRI_AVALIACAO),
        'higienizacao':triangular(*TRI_HIGIENIZACAO),
        'atendimento': triangular(*TRI_ATENDIMENTO),
        'professor':   triangular(*TRI_PROFESSOR),
        'radiologia':  triangular(*TRI_RADIOLOGIA),
        'finalizacao': triangular(*TRI_FINALIZACAO),
    }.get(tipo, 0))
 
 
# =====================================================
# ESCOLHER EQUIPE (menor fila)
# =====================================================
 
def escolher_equipe(equipes):
    return min(equipes, key=lambda eq: len(eq["estudantes"].queue))
 
 
# =====================================================
# MONITOR DE RECURSOS (utilização, filas, WIP)
# =====================================================
 
class Monitor:
    """
    Amostra periodicamente o estado dos recursos e registra WIP.
    Utilização = fração do tempo em que o recurso está ocupado.
    Fila média = média do comprimento da fila de espera.
    WIP = pacientes em atendimento no sistema a cada instante.
    """
 
    def __init__(self, env, intervalo=1.0):
        self.env       = env
        self.intervalo = intervalo
        self.amostras  = []          # [(tempo, {recurso: (uso, fila)}, wip)]
        self._recursos  = {}
        self._wip_ref   = None
 
    def registrar_recurso(self, nome, recurso):
        self._recursos[nome] = recurso
 
    def registrar_wip(self, referencia):
        """Referência a um contador de WIP externo (lista/dict mutável)."""
        self._wip_ref = referencia
 
    def run(self):
        while True:
            snapshot = {}
            for nome, res in self._recursos.items():
                uso  = res.count
                fila = len(res.queue)
                snapshot[nome] = (uso, fila)
            wip = self._wip_ref[0] if self._wip_ref else 0
            self.amostras.append((self.env.now, snapshot, wip))
            yield self.env.timeout(self.intervalo)
 
    def resumo(self, warmup):
        """
        Retorna dict com médias após o período de warm-up.
        """
        validos = [(t, s, w) for t, s, w in self.amostras if t >= warmup]
        if not validos:
            return {}
 
        result = {}
        nomes = validos[0][1].keys()
 
        for nome in nomes:
            usos  = [s[nome][0] for _, s, _ in validos]
            filas = [s[nome][1] for _, s, _ in validos]
            cap   = self._recursos[nome].capacity
            result[nome] = {
                "utilizacao_media": statistics.mean(usos) / cap,
                "fila_media":       statistics.mean(filas),
                "fila_max":         max(filas),
            }
 
        wips = [w for _, _, w in validos]
        result["__wip__"] = {
            "wip_medio": statistics.mean(wips),
            "wip_max":   max(wips),
        }
        return result
 
 
# =====================================================
# FUNÇÃO PRINCIPAL DA SIMULAÇÃO
# =====================================================
 
def executar_simulacao(semente, mostrar_logs=False):
 
    random.seed(semente)
 
    historico_pacientes = []   # tempos no sistema (pós warm-up)
    desistencias        = [0]  # contador de desistências
    wip_atual           = [0]  # pacientes ativos no sistema agora
 
    metricas = {
        "recepcao": [], "avaliacao": [], "kit": [],
        "enfermeiro": [], "atendimento": [], "professor": [],
        "radiologia": [], "finalizacao": []
    }
 
    def log(msg):
        if mostrar_logs:
            print(msg)
 
    # -------------------------------------------------
    # PROCESSO PACIENTE
    # -------------------------------------------------
 
    def paciente(env, nome, recepcao, equipes, kits,
                 enfermeiro, radiologia, finalizacao, monitor):
 
        chegada   = env.now
        pos_warmup = chegada >= TEMPO_WARMUP
 
        wip_atual[0] += 1
        log(f"\n{nome} chegou no minuto {env.now:.2f}")
 
        # ── RECEPÇÃO ──────────────────────────────────
        # Sem desistência na recepção (fila de chegada obrigatória)
        with recepcao.request() as req:
            t = env.now
            yield req
            espera = env.now - t
            if pos_warmup:
                metricas["recepcao"].append(espera)
            yield env.timeout(distribuicoes("recepcao"))
 
        # ── ESCOLHE EQUIPE ────────────────────────────
        equipe    = escolher_equipe(equipes)
        estudantes = equipe["estudantes"]
        professor_res = equipe["professor"]
 
        # ── AVALIAÇÃO (com desistência) ───────────────
        if len(estudantes.queue) >= LIMITE_DESISTENCIA:
            log(f"{nome} DESISTIU na avaliação (fila={len(estudantes.queue)})")
            desistencias[0] += 1
            wip_atual[0] -= 1
            return
 
        with estudantes.request(priority=1) as req:
            t = env.now
            yield req
            espera = env.now - t
            if pos_warmup:
                metricas["avaliacao"].append(espera)
            yield env.timeout(distribuicoes("avaliacao"))
 
        # ── KIT ──────────────────────────────────────
        if len(kits.queue) >= LIMITE_DESISTENCIA:
            log(f"{nome} DESISTIU no kit (fila={len(kits.queue)})")
            desistencias[0] += 1
            wip_atual[0] -= 1
            return
 
        with kits.request() as req:
            t = env.now
            yield req
            espera = env.now - t
            if pos_warmup:
                metricas["kit"].append(espera)
 
            # ── ENFERMEIRO ────────────────────────────
            with enfermeiro.request() as req2:
                t2 = env.now
                yield req2
                espera = env.now - t2
                if pos_warmup:
                    metricas["enfermeiro"].append(espera)
                yield env.timeout(distribuicoes("higienizacao"))
 
        # ── ATENDIMENTO (com desistência e controle de fim) ──
        if len(estudantes.queue) >= LIMITE_DESISTENCIA:
            log(f"{nome} DESISTIU no atendimento (fila={len(estudantes.queue)})")
            desistencias[0] += 1
            wip_atual[0] -= 1
            return
 
        # Não inicia atendimento se restar < MARGEM_FIM minutos
        if env.now + MARGEM_FIM > TEMPO_SIMULACAO:
            log(f"{nome} NÃO INICIOU atendimento (próximo do fim: t={env.now:.1f})")
            wip_atual[0] -= 1
            return
 
        with estudantes.request(priority=0) as req:
            t = env.now
            yield req
            espera = env.now - t
            if pos_warmup:
                metricas["atendimento"].append(espera)
            yield env.timeout(distribuicoes("atendimento"))
 
        # ── PROFESSOR ────────────────────────────────
        if random.random() < PROB_PROFESSOR:
            with professor_res.request() as req:
                t = env.now
                yield req
                espera = env.now - t
                if pos_warmup:
                    metricas["professor"].append(espera)
                yield env.timeout(distribuicoes("professor"))
 
        # ── RADIOLOGIA (com desistência) ──────────────
        if random.random() < PROB_RADIOLOGIA:
            if len(radiologia.queue) >= LIMITE_DESISTENCIA:
                log(f"{nome} DESISTIU na radiologia (fila={len(radiologia.queue)})")
                desistencias[0] += 1
                wip_atual[0] -= 1
                return
            with radiologia.request() as req:
                t = env.now
                yield req
                espera = env.now - t
                if pos_warmup:
                    metricas["radiologia"].append(espera)
                yield env.timeout(distribuicoes("radiologia"))
 
        # ── FINALIZAÇÃO ──────────────────────────────
        with finalizacao.request() as req:
            t = env.now
            yield req
            espera = env.now - t
            if pos_warmup:
                metricas["finalizacao"].append(espera)
            yield env.timeout(distribuicoes("finalizacao"))
 
        saida = env.now
        wip_atual[0] -= 1
 
        if pos_warmup:
            historico_pacientes.append(saida - chegada)
 
        log(f"{nome} finalizou no minuto {env.now:.2f}")
 
    # -------------------------------------------------
    # GERADOR DE PACIENTES (chegadas exponenciais)
    # -------------------------------------------------
 
    def gerar_pacientes(env, recepcao, equipes, kits,
                        enfermeiro, radiologia, finalizacao, monitor):
        i = 0
        while i < NUM_MAX_PACIENTES:
            yield env.timeout(distribuicoes("chegada"))
            i += 1
            env.process(
                paciente(env, f"Paciente {i}", recepcao, equipes,
                         kits, enfermeiro, radiologia, finalizacao, monitor)
            )
 
    # -------------------------------------------------
    # AMBIENTE E RECURSOS
    # -------------------------------------------------
 
    env       = simpy.Environment()
    recepcao  = simpy.Resource(env, capacity=2)
    kits      = simpy.Resource(env, capacity=4)
    enfermeiro = simpy.Resource(env, capacity=1)
    radiologia = simpy.Resource(env, capacity=1)
    finalizacao = simpy.Resource(env, capacity=1)
 
    equipes = []
    for i in range(4):
        equipes.append({
            "nome":      f"Equipe {i+1}",
            "estudantes": simpy.PriorityResource(env, capacity=3),
            "professor":  simpy.Resource(env, capacity=1),
        })
 
    # Monitor de recursos
    monitor = Monitor(env, intervalo=1.0)
    monitor.registrar_wip(wip_atual)
    monitor.registrar_recurso("recepcao",   recepcao)
    monitor.registrar_recurso("kits",       kits)
    monitor.registrar_recurso("enfermeiro", enfermeiro)
    monitor.registrar_recurso("radiologia", radiologia)
    monitor.registrar_recurso("finalizacao", finalizacao)
    for eq in equipes:
        monitor.registrar_recurso(eq["nome"] + "_estudantes", eq["estudantes"])
        monitor.registrar_recurso(eq["nome"] + "_professor",  eq["professor"])
 
    env.process(monitor.run())
    env.process(
        gerar_pacientes(env, recepcao, equipes, kits,
                        enfermeiro, radiologia, finalizacao, monitor)
    )
    env.run(until=TEMPO_SIMULACAO)
 
    # -------------------------------------------------
    # RESULTADOS DA RÉPLICA
    # -------------------------------------------------
 
    tempo_medio = statistics.mean(historico_pacientes) if historico_pacientes else 0
 
    # Gargalo = etapa com maior espera média pós warm-up
    ranking = sorted(
        [(statistics.mean(v), k) for k, v in metricas.items() if v],
        reverse=True
    )
    gargalo = ranking[0][1] if ranking else "nenhum"
 
    resumo_monitor = monitor.resumo(TEMPO_WARMUP)
 
    return tempo_medio, gargalo, desistencias[0], metricas, resumo_monitor
 
 
# =====================================================
# EXECUTA REPLICAÇÕES
# =====================================================
 
resultados        = []
gargalos          = []
total_desistencias = []
acum_metricas     = {k: [] for k in [
    "recepcao","avaliacao","kit","enfermeiro",
    "atendimento","professor","radiologia","finalizacao"
]}
acum_monitor      = []
 
for rep in range(NUM_REPLICACOES):
    ultima = (rep == NUM_REPLICACOES - 1)
 
    print(f"\n{'='*60}")
    print(f"REPLICAÇÃO {rep + 1}")
    print(f"{'='*60}")
 
    seed = random.randint(1, 10_000_000_000)
    print(f"Seed: {seed}")
 
    media, gargalo, desist, metricas_rep, resumo_mon = executar_simulacao(
        semente=seed, mostrar_logs=ultima
    )
 
    resultados.append(media)
    gargalos.append(gargalo)
    total_desistencias.append(desist)
    acum_monitor.append(resumo_mon)
 
    for k, v in metricas_rep.items():
        if v:
            acum_metricas[k].append(statistics.mean(v))
 
 
# =====================================================
# DIMENSIONAMENTO DE RODADAS
# =====================================================
 
media_global = statistics.mean(resultados)
desvio       = statistics.stdev(resultados)
erro_atual   = T_CRITICO * (desvio / math.sqrt(NUM_REPLICACOES))
 
# n necessário para a precisão desejada: n = (t * s / d)²
n_necessario = math.ceil((T_CRITICO * desvio / PRECISAO_DESEJADA) ** 2)
 
 
# =====================================================
# RELATÓRIO FINAL
# =====================================================
 
def ic(lista):
    """Intervalo de confiança 95% de uma lista."""
    if len(lista) < 2:
        return 0.0
    s   = statistics.stdev(lista)
    err = T_CRITICO * s / math.sqrt(len(lista))
    return err
 
print(f"\n{'='*60}")
print("RELATÓRIO FINAL")
print(f"{'='*60}")
 
# ── Tempo no sistema ──────────────────────────────────────
print(f"\n{'─'*40}")
print("TEMPO MÉDIO NO SISTEMA (pós warm-up)")
print(f"{'─'*40}")
print(f"  Média global : {media_global:.2f} min")
print(f"  Desvio padrão: {desvio:.2f} min")
print(f"  IC 95%       : {media_global:.2f} ± {erro_atual:.2f} min")
print(f"  [{media_global - erro_atual:.2f} , {media_global + erro_atual:.2f}] min")
 
# ── Dimensionamento ───────────────────────────────────────
print(f"\n{'─'*40}")
print("DIMENSIONAMENTO DE REPLICAÇÕES")
print(f"{'─'*40}")
print(f"  Rodadas executadas   : {NUM_REPLICACOES}")
print(f"  Precisão desejada    : ± {PRECISAO_DESEJADA:.1f} min (IC 95%)")
print(f"  Precisão obtida      : ± {erro_atual:.2f} min")
precisao_ok = erro_atual <= PRECISAO_DESEJADA
print(f"  Precisão atingida?   : {'SIM ✓' if precisao_ok else 'NÃO ✗'}")
print(f"  Replicações necessárias (estimativa): {n_necessario}")
 
# ── Desistências ──────────────────────────────────────────
print(f"\n{'─'*40}")
print("DESISTÊNCIAS DE FILA")
print(f"{'─'*40}")
media_desist = statistics.mean(total_desistencias)
err_desist   = ic(total_desistencias)
print(f"  Média por replicação : {media_desist:.2f} ± {err_desist:.2f}")
print(f"  Mínimo / Máximo      : {min(total_desistencias)} / {max(total_desistencias)}")
 
# ── Espera média por etapa ────────────────────────────────
print(f"\n{'─'*40}")
print("ESPERA MÉDIA POR ETAPA (pós warm-up)")
print(f"{'─'*40}")
print(f"  {'Etapa':<14} {'Média':>8} {'IC ±':>8}")
print(f"  {'-'*32}")
for etapa, vals in acum_metricas.items():
    if vals:
        m   = statistics.mean(vals)
        err = ic(vals)
        print(f"  {etapa:<14} {m:>7.2f}  {err:>7.2f}  min")
 
# ── Gargalo ───────────────────────────────────────────────
print(f"\n{'─'*40}")
print("GARGALO MAIS RECORRENTE")
print(f"{'─'*40}")
freq = {}
for g in gargalos:
    freq[g] = freq.get(g, 0) + 1
for i, (nome, qtd) in enumerate(
    sorted(freq.items(), key=lambda x: x[1], reverse=True)[:3], 1
):
    print(f"  {i}. {nome:<14} {qtd} ocorrências ({qtd/NUM_REPLICACOES*100:.1f}%)")
 
# ── Utilização e filas dos recursos ──────────────────────
print(f"\n{'─'*40}")
print("UTILIZAÇÃO E FILAS DOS RECURSOS (média entre replicações)")
print(f"{'─'*40}")
 
recursos_unicos = [k for k in acum_monitor[0].keys() if k != "__wip__"]
print(f"  {'Recurso':<30} {'Utiliz.':>8} {'Fila méd.':>10} {'Fila máx.':>10}")
print(f"  {'-'*60}")
 
for rec in recursos_unicos:
    utils  = [r[rec]["utilizacao_media"] for r in acum_monitor if rec in r]
    filas  = [r[rec]["fila_media"]       for r in acum_monitor if rec in r]
    fmaxs  = [r[rec]["fila_max"]         for r in acum_monitor if rec in r]
    if utils:
        u_med  = statistics.mean(utils)
        fq_med = statistics.mean(filas)
        fm_med = statistics.mean(fmaxs)
        print(f"  {rec:<30} {u_med:>7.1%}  {fq_med:>9.2f}  {fm_med:>9.1f}")
 
# ── WIP ───────────────────────────────────────────────────
print(f"\n{'─'*40}")
print("WIP — PACIENTES SIMULTÂNEOS NO SISTEMA")
print(f"{'─'*40}")
wips_med = [r["__wip__"]["wip_medio"] for r in acum_monitor if "__wip__" in r]
wips_max = [r["__wip__"]["wip_max"]   for r in acum_monitor if "__wip__" in r]
if wips_med:
    print(f"  WIP médio : {statistics.mean(wips_med):.2f} pacientes")
    print(f"  WIP máximo: {statistics.mean(wips_max):.1f} pacientes (média dos máximos)")
