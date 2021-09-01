"""
monodromy/xx_decompose/circuits.py

Tools for building optimal circuits out of XX interactions.

Inputs:
 + A set of native XX operations, described as strengths.
 + A right-angled path, computed using the methods in `xx_decompose/paths.py`.

Output:
 + A circuit which implements the target operation (expressed exactly as the
   exponential of `a XX + b YY + c ZZ`) using the native operations and local
   gates.
"""

from functools import reduce
import math
import numpy as np
from operator import itemgetter

import qiskit

from ..exceptions import NoBacksolution
from ..static.matrices import canonical_matrix, rz_matrix
from ..utilities import nearp, safe_arccos
from .paths import decomposition_hop
from .weyl import apply_reflection, apply_shift, canonical_rotation_circuit, \
    reflection_options, shift_options


def decompose_xxyy_into_xxyy_xx(a_target, b_target, a1, b1, a2):
    """
    Consumes a target canonical interaction CAN(a_target, b_target) and
    source interactions CAN(a1, b1), CAN(a2), then manufactures a
    circuit identity of the form

    CAN(a_target, b_target) = (Zr, Zs) CAN(a1, b1) (Zu, Zv) CAN(a2) (Zx, Zy).

    Returns the 6-tuple (r, s, u, v, x, y).
    """

    cplus, cminus = np.cos(a1 + b1), np.cos(a1 - b1)
    splus, sminus = np.sin(a1 + b1), np.sin(a1 - b1)
    ca, sa = np.cos(a2), np.sin(a2)

    uplusv = 1 / 2 * safe_arccos(
        cminus ** 2 * ca ** 2 + sminus ** 2 * sa ** 2 - np.cos(a_target - b_target) ** 2,
        2 * cminus * ca * sminus * sa
    )
    uminusv = 1 / 2 * safe_arccos(
        cplus ** 2 * ca ** 2 + splus ** 2 * sa ** 2 - np.cos(a_target + b_target) ** 2,
        2 * cplus * ca * splus * sa
    )

    u, v = (uplusv + uminusv) / 2, (uplusv - uminusv) / 2

    # NOTE: the target matrix is phase-free
    middle_matrix = reduce(np.dot, [
        canonical_matrix(a1, b1),
        np.kron(rz_matrix(u), rz_matrix(v)),
        canonical_matrix(a2),
    ])

    phase_solver = np.array([
        [1 / 4,  1 / 4,  1 / 4,  1 / 4, ],
        [1 / 4, -1 / 4, -1 / 4,  1 / 4, ],
        [1 / 4,  1 / 4, -1 / 4, -1 / 4, ],
        [1 / 4, -1 / 4,  1 / 4, -1 / 4, ],
    ])
    inner_phases = [
        np.angle(middle_matrix[0, 0]),
        np.angle(middle_matrix[1, 1]),
        np.angle(middle_matrix[1, 2]) + np.pi / 2,
        np.angle(middle_matrix[0, 3]) + np.pi / 2,
    ]
    r, s, x, y = np.dot(phase_solver, inner_phases)

    # If there's a phase discrepancy, need to conjugate by an extra Z/2 (x) Z/2.
    generated_matrix = reduce(np.dot, [
        np.kron(rz_matrix(r), rz_matrix(s)),
        middle_matrix,
        np.kron(rz_matrix(x), rz_matrix(y)),
    ])
    if ((abs(np.angle(generated_matrix[3, 0]) - np.pi / 2) < 0.01 and a_target > b_target) or
            (abs(np.angle(generated_matrix[3, 0]) + np.pi / 2) < 0.01 and a_target < b_target)):
        x += np.pi / 4
        y += np.pi / 4
        r -= np.pi / 4
        s -= np.pi / 4

    return r, s, u, v, x, y


def xx_circuit_step(
        source, strength, target, embodiment
):
    """
    Builds a single step in an XX-based circuit.

    `source` and `target` are positive canonical coordinates; `strength` is the
    interaction strength at this step in the circuit as a canonical coordinate
    (so that CX = RZX(pi/2) corresponds to pi/4); and `embodiment` is a QISKit
    circuit which enacts the canonical gate of the prescribed interaction
    `strength`.
    """

    permute_source_for_overlap, permute_target_for_overlap = None, None

    # apply all possible reflections, shifts to the source
    for source_reflection_name in reflection_options.keys():
        reflected_source_coord, source_reflection, reflection_phase_shift = \
            apply_reflection(source_reflection_name, source)
        for source_shift_name in shift_options.keys():
            shifted_source_coord, source_shift, shift_phase_shift = \
                apply_shift(source_shift_name, reflected_source_coord)

            # check for overlap, back out permutation
            source_shared, target_shared = None, None
            for i, j in [(0, 0), (0, 1), (0, 2),
                         (1, 0), (1, 1), (1, 2),
                         (2, 0), (2, 1), (2, 2)]:
                if nearp(shifted_source_coord[i], target[j],
                         modulus=np.pi):
                    source_shared, target_shared = i, j
                    break
            if source_shared is None:
                continue

            # pick out the other coordinates
            source_first, source_second = [x for x in [0, 1, 2]
                                           if x != source_shared]
            target_first, target_second = [x for x in [0, 1, 2]
                                           if x != target_shared]

            # check for arccos validity
            r, s, u, v, x, y = decompose_xxyy_into_xxyy_xx(
                float(target[target_first]),
                float(target[target_second]),
                float(shifted_source_coord[source_first]),
                float(shifted_source_coord[source_second]),
                float(strength),
            )
            if any([math.isnan(val) for val in (r, s, u, v, x, y)]):
                continue

            # OK: this combination of things works.
            # save the permutation which rotates the shared coordinate into ZZ.
            permute_source_for_overlap = canonical_rotation_circuit(
                source_first, source_second
            )
            permute_target_for_overlap = canonical_rotation_circuit(
                target_first, target_second
            )
            break

        if permute_source_for_overlap is not None:
            break

    if permute_source_for_overlap is None:
        raise NoBacksolution()

    # if (source_reflection_name != "no reflection" or
    #         source_shift_name != "no shift"):
    #     warnings.warn(f"Needed to use an extra Weyl reflection: "
    #                   f"{source_reflection_name} + {source_shift_name}.")

    prefix_circuit, affix_circuit = \
        qiskit.QuantumCircuit(2), qiskit.QuantumCircuit(2)

    # the basic formula we're trying to work with is:
    # target^p_t_f_o =
    #     rs * (source^s_reflection * s_shift)^p_s_f_o * uv * operation * xy
    # but we're rearranging it into the form
    #   target = affix source prefix
    # and computing just the prefix / affix circuits.

    # the outermost prefix layer comes from the (inverse) target permutation.
    prefix_circuit += permute_target_for_overlap.inverse()
    # the middle prefix layer comes from the local Z rolls.
    prefix_circuit.rz(2 * x, [0])
    prefix_circuit.rz(2 * y, [1])
    prefix_circuit.compose(embodiment, inplace=True)
    prefix_circuit.rz(2 * u, [0])
    prefix_circuit.rz(2 * v, [1])
    # the innermost prefix layer is source_reflection, shifted by source_shift,
    # finally conjugated by p_s_f_o.
    prefix_circuit += permute_source_for_overlap
    prefix_circuit += source_reflection
    prefix_circuit.global_phase += -np.log(reflection_phase_shift).imag
    prefix_circuit.global_phase += -np.log(shift_phase_shift).imag

    # the affix circuit is constructed in reverse.
    # first (i.e., innermost), we install the other half of the source
    # transformations and p_s_f_o.
    affix_circuit += source_reflection.inverse()
    affix_circuit += source_shift
    affix_circuit += permute_source_for_overlap.inverse()
    # then, the other local rolls in the middle.
    affix_circuit.rz(2 * r, [0])
    affix_circuit.rz(2 * s, [1])
    # finally, the other half of the p_t_f_o conjugation.
    affix_circuit += permute_target_for_overlap

    return {
        "prefix_circuit": prefix_circuit,
        "affix_circuit": affix_circuit
    }


def canonical_xx_circuit(target, strength_sequence, basis_embodiments):
    """
    Assembles a QISKit circuit from a specified `strength_sequence` of XX-type
    interactions which emulates the canonical gate at canonical coordinate
    `target`.  The circuits supplied by `basis_embodiments` are used to
    instantiate the individual XX actions.

    NOTE: The elements of `strength_sequence` are expected to be normalized so
        that np.pi/2 corresponds to RZX(np.pi/2) = CX; `target` is taken to be
        a positive canonical coordinate; and `basis_embodiments` maps
        `strength_sequence` elements to circuits which instantiate these gates.
    """
    # empty decompositions are easy!
    if 0 == len(strength_sequence):
        return qiskit.QuantumCircuit(2)

    # assemble the prefix / affix circuits
    prefix_circuit, affix_circuit = \
        qiskit.QuantumCircuit(2), qiskit.QuantumCircuit(2)
    while 1 < len(strength_sequence):
        source = decomposition_hop(target, strength_sequence)
        strength = strength_sequence[-1]

        preceding_prefix_circuit, preceding_affix_circuit = \
            itemgetter("prefix_circuit", "affix_circuit")(xx_circuit_step(
                source, strength / 2, target, basis_embodiments[strength]
            ))

        prefix_circuit.compose(preceding_prefix_circuit, inplace=True)
        affix_circuit.compose(preceding_affix_circuit, inplace=True,
                              front=True)

        target, strength_sequence = source, strength_sequence[:-1]

    circuit = prefix_circuit

    # lastly, deal with the "leading" gate.
    if target[0] <= np.pi / 4:
        circuit.compose(
            basis_embodiments[strength_sequence[0]],
            inplace=True
        )
    else:
        _, source_reflection, reflection_phase_shift = \
            apply_reflection("reflect XX, YY", [0, 0, 0])
        _, source_shift, shift_phase_shift = \
            apply_shift("X shift", [0, 0, 0])

        circuit += source_reflection
        circuit.compose(basis_embodiments[strength_sequence[0]],
                        inplace=True)
        circuit += source_reflection.inverse()
        circuit += source_shift
        circuit.global_phase += -np.log(shift_phase_shift).imag
        circuit.global_phase += -np.log(reflection_phase_shift).imag

    circuit.compose(affix_circuit, inplace=True)

    return circuit
