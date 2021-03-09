from flask import Flask, request, jsonify, abort
import dill
from flask_cors import CORS, cross_origin

import db
import mason
import ltspice2svg
import dpi
import circuit as cir


app = Flask(__name__)
app.config['DEBUG'] = True
CORS(app)


def circuit_to_dict(circuit: db.Circuit):

    import networkx as nx
    import sympy as sp

    sfg = dill.loads(circuit.sfg)

    for src, dest, weight in sfg.edges(data='weight'):
        weight = sfg.edges[src, dest]['weight']

        if isinstance(weight, sp.Expr):
            numeric = weight.subs(circuit.parameters)
        else:
            numeric = weight

        magnitude = float(sp.Abs(numeric))
        phase = float(sp.arg(numeric))
        sfg.edges[src, dest]['weight'] = {
            'symbolic': sp.latex(weight),
            'magnitude': magnitude,
            'phase': phase
        }

    return {
        '_id': str(circuit.id),
        'name': circuit.name,
        'parameters': circuit.parameters,
        'sfg': nx.cytoscape_data(sfg)
    }


@app.route('/circuits', methods=['POST'])
def create_circuit():
    netlist = request.json['netlist']
    op_point_log = request.json['opPointLog']
    schematic = request.json['schematic']

    # Instantiate circuit object from netlist and op_point_log.
    # Then, perform DPI analysis on circuit to get SFG.
    # For now, use hard-coded mock SFG data.


    from mock_data import sfg, parameters

    # circ = cir.Circuit.from_ltspice_netlist(netlist, op_point_log)
    # sfg = dpi.construct_graph(circ)

    circuit = db.Circuit(
        name='2n3904_common_emitter',
        netlist=netlist,
        op_point_log=op_point_log,
        parameters=parameters,
        sfg=dill.dumps(sfg),
        schematic=schematic
    ).save()

    # Convert circuit object to json form

    return circuit_to_dict(circuit)


@app.route('/circuits/<id>/transfer_function/<input>/<output>', methods=['GET'])
def get_circuit_transfer_function(id, input, output):
    circuit = db.Circuit.objects(id=id).first()
    tf = circuit.transfer_functions.filter(input=input, output=output).first()

    import sympy as sp

    if tf:
        return {'transfer_function': sp.latex(dill.loads(tf.symbolic))}

    try:
        sfg = dill.loads(circuit.sfg)
        symbolic = mason.transfer_function(sfg, input, output)
        numeric = symbolic.subs({k: v for k, v in circuit.parameters.items()
                                 if k != 'w'})
        numeric = sp.lambdify('w', numeric, 'numpy')
    except Exception as err:
        print(err)
        abort(404)

    circuit.transfer_functions.append(db.TransferFunction(
        input=input,
        output=output,
        symbolic=dill.dumps(symbolic),
        numeric=dill.dumps(numeric, recurse=True)
    ))

    circuit.save()

    return {'transfer_function': sp.latex(symbolic)}


@app.route('/circuits/<id>/transfer_function/<input>/<output>/bode', methods=['GET'])
def bode(id, input, output):
    try:
        start_freq = float(request.args.get('start_freq'))
        stop_freq = float(request.args.get('stop_freq'))
        steps_per_decade = int(request.args.get('steps_per_decade'))
    except:
        abort(400)

    circuit = db.Circuit.objects(id=id).first()
    tf = circuit.transfer_functions.filter(input=input, output=output).first()

    import sympy as sp

    if not tf:
        try:
            sfg = dill.loads(circuit.sfg)
            symbolic = mason.transfer_function(sfg, input, output)
            numeric = symbolic.subs({k: v for k, v in circuit.parameters.items()
                                     if k != 'w'})
            numeric = sp.lambdify('w', numeric, modules='numpy')
        except Exception as err:
            print(err)
            abort(404)

        circuit.transfer_functions.append(db.TransferFunction(
            input=input,
            output=output,
            symbolic=dill.dumps(symbolic),
            numeric=dill.dumps(numeric, recurse=True)
        ))

        circuit.save()

    else:
        numeric = dill.loads(tf.numeric)

    import math
    import numpy as np

    num_decades = int(math.log10(stop_freq / start_freq))
    num_steps = steps_per_decade * num_decades
    freq = 2 * np.pi * np.logspace(math.log10(start_freq),
                                   math.log10(stop_freq),
                                   num=num_steps, endpoint=True, base=10)

    out = numeric(freq)

    magnitude = np.abs(out).tolist()
    phase = np.angle(out, deg=True).tolist()

    return {
        'magnitude': magnitude,
        'phase': phase
    }


@app.route('/circuits/<id>', methods=['PATCH'])
def patch_circuit_parameters(id):
    circuit = db.Circuit.objects(id=id).first()

    if not circuit:
        abort(404)

    if not request.json.keys() <= circuit.parameters.keys():
        abort(400)

    circuit.parameters.update(request.json)

    circuit.transfer_functions.delete()
    circuit.save()

    return circuit_to_dict(circuit)


@app.route('/circuits/<id>', methods=['GET'])
def get_circuit(id):
    return circuit_to_dict(db.Circuit.objects(id=id).first())


@app.route('/circuits/<id>/schematic', methods=['GET'])
def get_schematic(id):
    circuit = db.Circuit.objects(id=id).first()

    if circuit.schematic is None:
        return {'svg': None}

    if circuit.svg is None:
        try:
            circuit.svg = ltspice2svg.asc_to_svg(circuit.schematic)
            circuit.save()
        except Exception as e:
            print(e)
            abort(400)

    return circuit.svg


app.run()

