"""Pure-Python implementation of CMA-ES (no numpy/scipy dependency)."""

import math
import random


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _norm(a):
    return math.sqrt(_dot(a, a))


def _mat_transpose(A):
    n = len(A)
    return [[A[i][j] for i in range(n)] for j in range(n)]


def _mat_mul(A, B):
    n = len(A)
    BT = _mat_transpose(B)
    return [[_dot(A[i], BT[j]) for j in range(n)] for i in range(n)]


def _mat_vec(A, v):
    return [_dot(row, v) for row in A]


def _vec_outer(a, b):
    return [[a[i] * b[j] for j in range(len(b))] for i in range(len(a))]


def _symmetrize(C):
    n = len(C)
    for i in range(n):
        for j in range(i + 1, n):
            avg = (C[i][j] + C[j][i]) / 2.0
            C[i][j] = C[j][i] = avg
    return C


def _eig_jacobi(A, max_sweeps=100, tol=1e-15):
    """Jacobi eigen-decomposition for a symmetric matrix A.

    Returns (eigenvalues, eigenvector_matrix) where eigenvector_matrix columns
    are the eigenvectors.
    """
    n = len(A)
    V = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    M = [row[:] for row in A]

    for _ in range(max_sweeps):
        max_off = 0.0
        p, q = 0, 1
        for i in range(n):
            for j in range(i + 1, n):
                if abs(M[i][j]) > max_off:
                    max_off = abs(M[i][j])
                    p, q = i, j
        if max_off < tol:
            break

        tau = (M[q][q] - M[p][p]) / (2.0 * M[p][q])
        if tau >= 0:
            t = 1.0 / (tau + math.sqrt(1.0 + tau * tau))
        else:
            t = -1.0 / (-tau + math.sqrt(1.0 + tau * tau))
        c = 1.0 / math.sqrt(1.0 + t * t)
        s = t * c

        # Update M.
        app = M[p][p]
        aqq = M[q][q]
        apq = M[p][q]
        M[p][p] = c * c * app - 2 * c * s * apq + s * s * aqq
        M[q][q] = s * s * app + 2 * c * s * apq + c * c * aqq
        M[p][q] = M[q][p] = 0.0

        for i in range(n):
            if i != p and i != q:
                aip = M[i][p]
                aiq = M[i][q]
                M[i][p] = M[p][i] = c * aip - s * aiq
                M[i][q] = M[q][i] = s * aip + c * aiq

            vip = V[i][p]
            viq = V[i][q]
            V[i][p] = c * vip - s * viq
            V[i][q] = s * vip + c * viq

    eigenvalues = [M[i][i] for i in range(n)]
    return eigenvalues, V


def _decompose(C, eps=1e-10):
    """Return eigenvalues and sqrt(eigenvalues) for sampling."""
    eigvals, B = _eig_jacobi(C)
    D = [math.sqrt(max(e, eps)) for e in eigvals]
    return B, D


class CMAEvolutionStrategy:
    def __init__(
        self,
        x0,
        sigma0,
        max_evals,
        lambda_=None,
        mu=None,
        seed=None,
        best_f=None,
        best_x=None,
    ):
        """Initialize CMA-ES.  If ``best_f`` and ``best_x`` are supplied, they
        seed the global best known candidate.  This is used when warm-starting
        from a previous run so the search never loses a good solution."""
        self.n = len(x0)
        if self.n == 0:
            raise ValueError("x0 must have at least one dimension")

        if seed is not None:
            random.seed(seed)

        self.lambd = lambda_ or 4 + math.floor(3 * math.log(self.n))
        if self.lambd < 2:
            self.lambd = 2
        self.mu = mu or self.lambd // 2
        if self.mu < 1:
            self.mu = 1

        # Recombination weights (positive, decreasing, sum to 1).
        raw = [math.log((self.lambd + 1) / 2.0) - math.log(i) for i in range(1, self.mu + 1)]
        s = sum(raw)
        self.weights = [w / s for w in raw]
        self.mueff = 1.0 / sum(w * w for w in self.weights)

        # Standard default parameters.
        self.cc = (4.0 + self.mueff / self.n) / (self.n + 4.0 + 2.0 * self.mueff / self.n)
        self.cs = (self.mueff + 2.0) / (self.n + self.mueff + 5.0)
        self.c1 = 2.0 / ((self.n + 1.3) ** 2 + self.mueff)
        self.cmu = min(
            1.0 - self.c1,
            2.0 * (self.mueff - 2.0 + 1.0 / self.mueff) / ((self.n + 2.0) ** 2 + self.mueff),
        )
        self.damps = (
            1.0
            + 2.0 * max(0.0, math.sqrt((self.mueff - 1.0) / (self.n + 1.0)) - 1.0)
            + self.cs
        )

        # Expected norm of a standard normal vector.
        self.chiN = math.sqrt(2.0) * math.gamma((self.n + 1.0) / 2.0) / math.gamma(self.n / 2.0)

        self.m = [float(v) for v in x0]
        self.sigma = float(sigma0)
        self.C = [[1.0 if i == j else 0.0 for j in range(self.n)] for i in range(self.n)]
        self.B, self.D = _decompose(self.C)
        self.pc = [0.0] * self.n
        self.ps = [0.0] * self.n
        self.counteval = 0
        self.max_evals = max_evals

        self.generation = 0
        self.best_x = list(best_x) if best_x is not None and best_f is not None else None
        self.best_f = float(best_f) if best_x is not None and best_f is not None else float("inf")
        self.history = []  # (generation, mean_f, best_f)

    def _sample(self):
        z = [random.gauss(0.0, 1.0) for _ in range(self.n)]
        # y = B @ (D * z)
        dz = [self.D[i] * z[i] for i in range(self.n)]
        y = _mat_vec(self.B, dz)
        x = [self.m[i] + self.sigma * y[i] for i in range(self.n)]
        return x, y, z

    def ask(self):
        """Return a list of (x, y, z) candidate tuples."""
        return [self._sample() for _ in range(self.lambd)]

    def tell(self, solutions, fitnesses):
        """Update the CMA-ES state after evaluating all candidates.

        `solutions` is the list returned by ask(); `fitnesses` is a parallel list
        of float values (lower is better).
        """
        if len(solutions) != self.lambd or len(fitnesses) != self.lambd:
            raise ValueError("solutions and fitnesses must match lambda")

        self.counteval += self.lambd
        self.generation += 1

        # Sort by fitness (best first).
        ranked = sorted(range(self.lambd), key=lambda i: fitnesses[i])
        best_idx = ranked[0]
        gen_best_f = fitnesses[best_idx]
        if gen_best_f < self.best_f:
            self.best_f = gen_best_f
            self.best_x = solutions[best_idx][0][:]

        # Recombination using the best mu candidates.
        y_w = [0.0] * self.n
        z_w = [0.0] * self.n
        selected_y = []
        for i in range(self.mu):
            idx = ranked[i]
            _, y, z = solutions[idx]
            w = self.weights[i]
            for j in range(self.n):
                y_w[j] += w * y[j]
                z_w[j] += w * z[j]
            selected_y.append(y)

        # Update mean.
        self.m = [self.m[i] + self.sigma * y_w[i] for i in range(self.n)]

        # Step-size control (Cumulative Step-size Adaptation).
        self.ps = [
            (1.0 - self.cs) * self.ps[i] + math.sqrt(self.cs * (2.0 - self.cs) * self.mueff) * z_w[i]
            for i in range(self.n)
        ]
        ps_norm = _norm(self.ps)
        denom = self.chiN
        self.sigma *= math.exp((self.cs / self.damps) * (ps_norm / denom - 1.0))

        # Stagnation detection for pc update.
        hsig = (
            ps_norm
            / math.sqrt(1.0 - (1.0 - self.cs) ** (2.0 * self.counteval / self.lambd))
            / self.chiN
            < 1.4 + 2.0 / (self.n + 1.0)
        )

        # Rank-one update path.
        if hsig:
            self.pc = [
                (1.0 - self.cc) * self.pc[i]
                + math.sqrt(self.cc * (2.0 - self.cc) * self.mueff) * y_w[i]
                for i in range(self.n)
            ]
        else:
            self.pc = [(1.0 - self.cc) * self.pc[i] for i in range(self.n)]

        # Covariance matrix update: (1-c1-cmu) C + c1 pc pc^T + cmu sum w_i y_i y_i^T.
        n = self.n
        factor = 1.0 - self.c1 - self.cmu
        newC = [[factor * self.C[i][j] for j in range(n)] for i in range(n)]

        # Rank-one.
        for i in range(n):
            for j in range(n):
                newC[i][j] += self.c1 * self.pc[i] * self.pc[j]

        # Rank-mu.
        for k in range(self.mu):
            y = selected_y[k]
            w = self.weights[k]
            for i in range(n):
                for j in range(n):
                    newC[i][j] += self.cmu * w * y[i] * y[j]

        _symmetrize(newC)
        self.C = newC

        # Decompose C for next sampling.
        self.B, self.D = _decompose(self.C)

        mean_f = sum(fitnesses) / len(fitnesses)
        self.history.append((self.generation, mean_f, gen_best_f, self.best_f))

    def result(self):
        return {
            "x": self.best_x,
            "fun": self.best_f,
            "nfev": self.counteval,
            "sigma": self.sigma,
        }
