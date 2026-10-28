# server.py  —  back-end Flask : NLP -> SPARQL, exécution SPARQL (SELECT + UPDATE) + sauvegarde automatique
import os
import re
import shutil
from flask import Flask, request, jsonify, send_from_directory, Response
from rdflib import Graph, URIRef, Literal, RDF
from nlp2sparql import Translator, PREFIXES, NS, local

HERE = os.path.dirname(os.path.abspath(__file__))
OWL_FILE = os.path.join(HERE, "AssuranceOnto.owl")
BACKUP = OWL_FILE.replace(".owl", ".backup.owl")

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
        res = g.query(q)
        cols = [str(v) for v in res.vars]
        rows = [[("" if c is None else str(c)).replace(NS, "ns:").replace("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdf:")
                 for c in r] for r in res]
        return jsonify(cols=cols, rows=rows)
    except Exception as e:
        return jsonify(error=str(e)), 400


@app.route("/save", methods=["POST"])
def save():
    save_to_file()
    return jsonify(message="Sauvegardé dans AssuranceOnto.owl")


@app.route("/vocab")
def vocab():
    """vocabulaire de l'ontologie (autocomplétion de l'interface)"""
    return jsonify(individus=sorted(translator.itypes), classes=sorted(translator.classes),
                   proprietes=sorted(translator.dprops))


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
