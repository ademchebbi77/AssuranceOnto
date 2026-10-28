# server.py  —  back-end Flask : NLP -> SPARQL, exécution SPARQL (SELECT + UPDATE) + sauvegarde automatique
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from flask import Flask, request, jsonify, send_from_directory, Response
from rdflib import Graph, URIRef, Literal, RDF, RDFS, OWL
from nlp2sparql import Translator, PREFIXES, NS, local, DEFAULT_NUM

HERE = os.path.dirname(os.path.abspath(__file__))
OWL_FILE = os.path.join(HERE, "AssuranceOnto.owl")
BACKUP = OWL_FILE.replace(".owl", ".backup.owl")


def load_env(path=os.path.join(HERE, ".env")):
    """lit le fichier .env (CLE=valeur) sans dépendance externe"""
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
COHERE_KEY = os.environ.get("COHERE_API_KEY", "")
COHERE_MODEL = os.environ.get("COHERE_MODEL", "command-a-plus-05-2026")

g = Graph()
g.parse(OWL_FILE, format="xml")
translator = Translator(g)
app = Flask(__name__)


def save_to_file():
    """Écrit le graphe dans AssuranceOnto.owl (copie de sécurité du fichier d'origine faite une seule fois)."""
    if not os.path.exists(BACKUP):
        shutil.copy(OWL_FILE, BACKUP)
    g.serialize(destination=OWL_FILE, format="xml")


@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/nlp", methods=["POST"])
def nlp_route():
    q = (request.get_json() or {}).get("question", "")
    try:
        sparql, info = translator.translate(q)
        return jsonify(sparql=sparql, analyse=info)
    except Exception as e:
        return jsonify(error=str(e)), 400


@app.route("/sparql", methods=["POST"])
def sparql_route():
    q = (request.get_json() or {}).get("query", "")
    body = re.sub(r"(?im)^\s*(PREFIX[^\n]*|#[^\n]*)\n", "", q).strip()
    if not q.lstrip().upper().startswith("PREFIX"):
        q = PREFIXES + q
    try:
        if re.match(r"(?i)(INSERT|DELETE|WITH|LOAD|CLEAR)", body):
            g.update(q)
            translator.__init__(g)              # rafraîchit le vocabulaire (nouveaux individus)
            save_to_file()                      # sauvegarde automatique dans AssuranceOnto.owl
            return jsonify(message="Mise à jour effectuée et sauvegardée dans AssuranceOnto.owl", triples=len(g))
        return jsonify(**table(g.query(q)))
    except Exception as e:
        return jsonify(error=str(e)), 400


def table(res):
    """résultat SPARQL -> colonnes + lignes (URI abrégées en ns: / rdf:)"""
    rows = [[("" if c is None else str(c)).replace(NS, "ns:").replace("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdf:")
             for c in r] for r in res]
    return {"cols": [str(v) for v in res.vars], "rows": rows}


# ------------------------------------------------------------------ filtrage multicritère
def label(name):
    return next((str(l) for l in g.objects(URIRef(NS + name), RDFS.label)), name)


def inverses():
    inv = {}
    for a, b in g.subject_objects(OWL.inverseOf):
        inv[local(a)], inv[local(b)] = local(b), local(a)
    return inv


def kind_of(prop):
    rng = translator.dprops.get(prop)
    return "nombre" if rng in ("integer", "decimal", "int") else "date" if rng in ("date", "dateTime") else "texte"


@app.route("/schema")
def schema():
    """classes et propriétés utilisables pour construire un filtre (lues dans l'ontologie)"""
    T = translator
    props = {}
    for p in T.dprops:
        k = kind_of(p)
        vals = sorted({str(o) for o in g.objects(None, URIRef(NS + p))})[:40] if k == "texte" else []
        props[p] = {"label": label(p), "kind": k, "domain": T.pdom.get(p, ""), "values": vals}
    for d, r, p in T.oprops:
        props[p] = {"label": label(p), "kind": "relation", "domain": d,
                    "values": sorted(i for i, types in T.itypes.items() if r in types)}
    tops = [c for c in T.classes if not any(str(o).startswith(NS) for o in g.objects(URIRef(NS + c), RDFS.subClassOf))]
    classes = []
    for c in sorted(T.classes, key=lambda c: label(c).lower()):
        top = next((t for t in tops if t in T.supers(c)), c)
        classes.append({"id": c, "label": label(c), "top": label(top), "props": sorted(
            (p for p, v in props.items() if v["domain"] and T.compatible(v["domain"], c)),
            key=lambda p: (props[p]["kind"] == "relation", props[p]["label"].lower()))})
    return jsonify(classes=classes, props=props)


NUM_OPS = {"=": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}


def literal(prop, value):
    """valeur saisie -> littéral typé (contrôlée : aucune injection possible dans la requête)"""
    v = str(value).strip().replace(",", ".")
    if kind_of(prop) == "nombre":
        if not re.fullmatch(r"-?\d+(\.\d+)?", v):
            raise ValueError(f"nombre attendu pour « {label(prop)} »")
        return translator._lit(prop, v)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise ValueError("date attendue (AAAA-MM-JJ)")
    return translator._lit(prop, v)


@app.route("/filter", methods=["POST"])
def filter_route():
    """construit et exécute une requête SPARQL à partir du formulaire de filtrage"""
    T, d = translator, request.get_json() or {}
    cls = d.get("classe", "")
    if cls not in T.classes:
        return jsonify(error="Choisissez une classe."), 400
    opnames = {p for _, _, p in T.oprops}
    inv = inverses()
    where, cols = [T.type_pattern(cls)], []
    try:
        for i, c in enumerate(d.get("conditions", [])[:10]):
            p, op, val = c.get("prop", ""), c.get("op", ""), str(c.get("valeur", "")).strip()
            v = f"?f{i}"
            if p in T.dprops:
                k = kind_of(p)
                if val == "":
                    raise ValueError(f"valeur manquante pour « {label(p)} »")
                where.append(f"?x ns:{p} {v} .")
                if k in ("nombre", "date"):
                    if op == "entre":
                        where.append(f"FILTER ({v} >= {literal(p, val)} && {v} <= {literal(p, c.get('valeur2', ''))})")
                    elif op in NUM_OPS:
                        where.append(f"FILTER ({v} {NUM_OPS[op]} {literal(p, val)})")
                    else:
                        raise ValueError(f"opérateur inconnu : {op}")
                else:
                    s = val.lower().replace("\\", "\\\\").replace('"', '\\"')
                    test = {"contient": f'CONTAINS(LCASE(STR({v})), "{s}")', "commence": f'STRSTARTS(LCASE(STR({v})), "{s}")',
                            "egal": f'LCASE(STR({v})) = "{s}"', "different": f'LCASE(STR({v})) != "{s}"'}.get(op)
                    if not test:
                        raise ValueError(f"opérateur inconnu : {op}")
                    where.append(f"FILTER ({test})")
                if p not in cols:
                    cols.append(p)
            elif p in opnames:
                if val not in T.itypes:
                    raise ValueError(f"individu inconnu : {val}")
                path = f"(ns:{p}|^ns:{inv[p]})" if p in inv else f"ns:{p}"
                if op == "est":
                    where.append(f"?x {path} ns:{val} .")
                elif op == "nest":
                    where.append(f"FILTER NOT EXISTS {{ ?x {path} ns:{val} }}")
                else:
                    raise ValueError(f"opérateur inconnu : {op}")
            else:
                raise ValueError(f"propriété inconnue : {p}")
    except ValueError as e:
        return jsonify(error=f"Filtre invalide : {e}"), 400
    tri, sens = d.get("tri", ""), "DESC" if d.get("sens") == "desc" else "ASC"
    if tri and tri not in T.dprops:
        return jsonify(error="Propriété de tri inconnue."), 400
    if tri and tri not in cols:
        cols.append(tri)
    if not cols and T._default(DEFAULT_NUM, cls):
        cols.append(T._default(DEFAULT_NUM, cls))
    where += [f"OPTIONAL {{ ?x ns:{p} ?{p} }}" for p in cols] + [T.NOM]
    limit = max(1, min(int(d.get("limite") or 100), 500))
    q = (f"{PREFIXES}SELECT DISTINCT ?nom {' '.join('?' + p for p in cols)} WHERE {{\n  " + "\n  ".join(where) + "\n}"
         + (f"\nORDER BY {sens}(?{tri})" if tri else "") + f"\nLIMIT {limit}")
    try:
        return jsonify(sparql=q, **table(g.query(q)))
    except Exception as e:
        return jsonify(error=str(e), sparql=q), 400


@app.route("/save", methods=["POST"])
def save():
    save_to_file()
    return jsonify(message="Sauvegardé dans AssuranceOnto.owl")


@app.route("/vocab")
def vocab():
    """vocabulaire de l'ontologie (autocomplétion de l'interface)"""
    return jsonify(individus=sorted(translator.itypes), classes=sorted(translator.classes),
                   proprietes=sorted(translator.dprops), ia=bool(COHERE_KEY))


EXPLAIN_PROMPT = """Tu es l'assistant d'une application d'assurance. Un module NLP a déjà interrogé l'ontologie AssuranceOnto.
Explique le résultat à l'utilisateur en 1 à 3 phrases claires, en français, sans jargon technique (pas de SPARQL, pas de "ns:").
Utilise UNIQUEMENT les données ci-dessous : n'invente rien, n'ajoute aucune information extérieure.
Les noms comme "Sinistre 001" ou "Ahmed BenSalah" sont des individus de l'ontologie ; garde-les tels quels.
S'il n'y a aucune ligne, dis simplement qu'aucun résultat ne correspond.

Question : {question}
Colonnes : {cols}
Lignes ({n} au total{trunc}) :
{rows}"""


@app.route("/explain", methods=["POST"])
def explain():
    """explication des résultats en une phrase (IA Cohere) — ne génère ni ne modifie aucune requête"""
    if not COHERE_KEY:
        return jsonify(error="Assistant IA désactivé : ajoutez COHERE_API_KEY dans le fichier .env"), 400
    d = request.get_json() or {}
    cols, rows = d.get("cols", []), d.get("rows", [])
    prompt = EXPLAIN_PROMPT.format(question=str(d.get("question", ""))[:300], cols=", ".join(map(str, cols)),
                                   n=len(rows), trunc=", 30 premières affichées" if len(rows) > 30 else "",
                                   rows="\n".join(" | ".join(map(str, r)) for r in rows[:30]) or "(aucune)")
    body = json.dumps({"model": COHERE_MODEL, "temperature": 0.2, "max_tokens": 220,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.cohere.com/v2/chat", data=body, headers={
        "Authorization": f"Bearer {COHERE_KEY}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            out = json.loads(r.read())
        text = "".join(c.get("text", "") for c in out["message"]["content"] if c.get("type") == "text").strip()
        return jsonify(texte=text)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:200]
        msg = "clé API invalide" if e.code == 401 else f"erreur Cohere {e.code} : {detail}"
        return jsonify(error=f"Assistant IA indisponible ({msg})"), 502
    except Exception as e:
        return jsonify(error=f"Assistant IA indisponible ({e})"), 502


def ns_type(node):
    """classe la plus précise d'un individu (pour la couleur du nœud)"""
    types = [local(t) for t in g.objects(node, RDF.type) if str(t).startswith(NS)]
    return types[0] if types else ""


@app.route("/graph", methods=["POST"])
def graph():
    """voisinage d'un individu : ses triplets sortants et entrants (graphe de connaissances)"""
    name = (request.get_json() or {}).get("node", "")
    node = URIRef(NS + name)
    if name not in translator.itypes:
        return jsonify(error=f"Individu inconnu : {name}"), 400
    nodes = {name: {"id": name, "label": name.replace("_", " "), "group": ns_type(node), "kind": "ind"}}
    edges = []
    for s, p, o in list(g.triples((node, None, None))) + list(g.triples((None, None, node))):
        if p == RDF.type or not str(p).startswith(NS):
            continue
        if isinstance(o, Literal):
            oid = f"{local(s)}·{local(p)}"
            nodes[oid] = {"id": oid, "label": str(o).replace("T00:00:00", ""), "group": "", "kind": "lit"}
        elif str(o).startswith(NS) and str(s).startswith(NS):
            oid = local(o)
            other = o if s == node else s
            nodes.setdefault(local(other), {"id": local(other), "label": local(other).replace("_", " "),
                                            "group": ns_type(other), "kind": "ind"})
        else:
            continue
        edges.append({"from": local(s), "to": oid, "label": local(p)})
    return jsonify(nodes=list(nodes.values()), edges=edges)


@app.route("/export/<fmt>")
def export(fmt):
    """téléchargement de l'ontologie : turtle, json-ld ou rdf/xml"""
    formats = {"ttl": ("turtle", "text/turtle"), "jsonld": ("json-ld", "application/ld+json"),
               "owl": ("xml", "application/rdf+xml")}
    if fmt not in formats:
        return jsonify(error="Format inconnu (ttl, jsonld, owl)"), 400
    rdf_fmt, mime = formats[fmt]
    data = g.serialize(format=rdf_fmt)
    return Response(data, mimetype=mime,
                    headers={"Content-Disposition": f"attachment; filename=AssuranceOnto.{fmt}"})


if __name__ == "__main__":
    app.run(debug=False)
