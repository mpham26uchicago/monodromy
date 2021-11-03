"""
test/xx_decompose/test_qiskit.py

Tests for monodromy/xx_decompose/qiskit.py .
"""

import qiskit
from qiskit.quantum_info.operators import Operator

import ddt
import unittest

import random
from statistics import mean

from scipy.stats import unitary_group

from monodromy.static.matrices import canonical_matrix
from monodromy.xx_decompose.qiskit import *

epsilon = 0.001


@ddt.ddt
class TestMonodromyQISKit(unittest.TestCase):
    """Check QISKit routines."""

    decomposer = MonodromyZXDecomposer(euler_basis="PSX")

    def __init__(self, *args, seed=42, **kwargs):
        super().__init__(*args, **kwargs)
        random.seed(seed)
        np.random.seed(seed)

    def test_random_compilation(self):
        """Test that compilation gives correct results."""
        for _ in range(100):
            u = unitary_group.rvs(4)
            u /= np.linalg.det(u) ** (1 / 4)

            # decompose into CX, CX/2, and CX/3
            circuit = self.decomposer(u, approximate=False)
            v = Operator(circuit).data

            self.assertTrue(np.all(u - v < epsilon))

    def test_compilation_determinism(self):
        """Test that compilation is stable under multiple calls."""
        for _ in range(10):
            u = unitary_group.rvs(4)
            u /= np.linalg.det(u) ** (1 / 4)

            # decompose into CX, CX/2, and CX/3
            circuit1 = self.decomposer(u, approximate=False)
            circuit2 = self.decomposer(u, approximate=False)

            self.assertEqual(circuit1, circuit2)

    @ddt.data(np.pi / 3, np.pi / 5, np.pi / 2)
    def test_default_embodiment(self, angle):
        """Test that _default_embodiment actually does yield XX gates."""
        embodiment = self.decomposer._default_embodiment(angle)
        embodiment_matrix = Operator(embodiment).data
        self.assertTrue(np.all(
            canonical_matrix(angle, 0, 0) - embodiment_matrix < epsilon
        ))

    def test_compilation_improvement(self):
        """Test that compilation to CX, CX/2, CX/3 improves over CX alone."""
        strength_table = self.decomposer._strength_to_infidelity(
            basis_fidelity=None, approximate=True,
        )
        limited_strength_table = {
            np.pi / 2: strength_table[np.pi / 2]
        }

        clever_costs = []
        naive_costs = []
        for _ in range(200):
            u = unitary_group.rvs(4)
            u /= np.linalg.det(u) ** (1 / 4)

            weyl_decomposition = TwoQubitWeylDecomposition(u)
            target = [getattr(weyl_decomposition, x) for x in ("a", "b", "c")]
            if target[-1] < -epsilon:
                target = [np.pi / 2 - target[0], target[1], -target[2]]

            # decompose into CX, CX/2, and CX/3
            clever_costs.append(self.decomposer._best_decomposition(
                target, strength_table
            )["cost"])
            naive_costs.append(self.decomposer._best_decomposition(
                target, limited_strength_table
            )["cost"])

        # the following are taken from Fig 14 of the XX synthesis paper
        self.assertAlmostEqual(mean(clever_costs), 1.445e-2, delta=5e-3)
        self.assertAlmostEqual(mean(naive_costs), 2.058e-2, delta=5e-3)