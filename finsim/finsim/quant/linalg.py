"""Small dense linear-algebra and optimisation helpers (lists of floats, lists of lists)."""
import math


def dot(a, b):
    """Inner product of two vectors."""
    return sum(x * y for x, y in zip(a, b))


def transpose(A):
    """Transpose of a matrix."""
    return [list(col) for col in zip(*A)]


def matmul(A, B):
    """Matrix product A·B."""
    Bt = transpose(B)
    return [[dot(row, col) for col in Bt] for row in A]


def matvec(A, x):
    """Matrix-vector product A·x."""
    return [dot(row, x) for row in A]


def identity(n):
    """n×n identity matrix."""
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def outer(a, b):
    """Outer product a·bᵀ."""
    return [[x * y for y in b] for x in a]


def gram(X):
    """XᵀX for a design matrix given as a list of rows."""
    k = len(X[0]) if X else 0
    G = [[0.0] * k for _ in range(k)]
    for row in X:
        for i in range(k):
            ri = row[i]
            if ri == 0.0:
                continue
            Gi = G[i]
            for j in range(i, k):
                Gi[j] += ri * row[j]
    for i in range(k):
        for j in range(i):
            G[i][j] = G[j][i]
    return G


def xty(X, y):
    """Xᵀy for a design matrix given as a list of rows."""
    k = len(X[0]) if X else 0
    out = [0.0] * k
    for row, yi in zip(X, y):
        for i in range(k):
            out[i] += row[i] * yi
    return out


def solve(A, b):
    """Solve A·x = b by Gaussian elimination with partial pivoting. Raises ValueError if singular."""
    n = len(A)
    multi = isinstance(b[0], (list, tuple)) if b else False
    M = [list(map(float, A[i])) + (list(map(float, b[i])) if multi else [float(b[i])]) for i in range(n)]
    width = len(M[0])
    scale = max((abs(v) for row in A for v in row), default=0.0) or 1.0
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) <= 1e-13 * scale:
            raise ValueError("singular matrix")
        if p != c:
            M[c], M[p] = M[p], M[c]
        piv = M[c][c]
        rowc = M[c]
        for r in range(c + 1, n):
            f = M[r][c] / piv
            if f != 0.0:
                rowr = M[r]
                for j in range(c, width):
                    rowr[j] -= f * rowc[j]
    X = [[0.0] * (width - n) for _ in range(n)]
    for i in range(n - 1, -1, -1):
        for j in range(width - n):
            s = M[i][n + j] - sum(M[i][k] * X[k][j] for k in range(i + 1, n))
            X[i][j] = s / M[i][i]
    return X if multi else [row[0] for row in X]


def inverse(A):
    """Matrix inverse via Gaussian elimination."""
    return solve(A, identity(len(A)))


def eigh_sym(A, tol=1e-12, max_sweeps=100):
    """Eigenvalues and eigenvectors of a symmetric matrix (cyclic Jacobi).

    Returns (values, vectors) sorted by value descending; vectors[i] is the i-th eigenvector.
    """
    n = len(A)
    a = [list(map(float, row)) for row in A]
    v = identity(n)
    for _ in range(max_sweeps):
        off = sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < tol * tol:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    vals = [a[i][i] for i in range(n)]
    order = sorted(range(n), key=lambda i: -vals[i])
    vecs = []
    for i in order:
        vec = [v[k][i] for k in range(n)]
        # sign convention: largest component positive
        big = max(range(n), key=lambda k: abs(vec[k]))
        if vec[big] < 0:
            vec = [-x for x in vec]
        vecs.append(vec)
    return [vals[i] for i in order], vecs


def power_iteration(A, iters=500, tol=1e-12):
    """Dominant eigenvalue and eigenvector of a square matrix by power iteration."""
    n = len(A)
    x = [1.0 / math.sqrt(n)] * n
    lam = 0.0
    for _ in range(iters):
        y = matvec(A, x)
        norm = math.sqrt(dot(y, y))
        if norm == 0.0:
            return 0.0, x
        y = [yi / norm for yi in y]
        new = dot(y, matvec(A, y))
        if abs(new - lam) < tol:
            lam = new
            x = y
            break
        lam, x = new, y
    return lam, x


def nelder_mead(f, x0, step=0.1, max_iter=400, tol=1e-10):
    """Minimise f(x) from x0 with the Nelder-Mead simplex. Returns (x, f(x))."""
    n = len(x0)
    pts = [list(x0)]
    for i in range(n):
        p = list(x0)
        p[i] += step if p[i] == 0 else step * max(1.0, abs(p[i]))
        pts.append(p)
    vals = [f(p) for p in pts]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda i: vals[i])
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) <= tol * (abs(vals[0]) + tol):
            break
        cen = [sum(p[j] for p in pts[:-1]) / n for j in range(n)]
        xr = [cen[j] + (cen[j] - pts[-1][j]) for j in range(n)]
        fr = f(xr)
        if fr < vals[0]:
            xe = [cen[j] + 2.0 * (cen[j] - pts[-1][j]) for j in range(n)]
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = [cen[j] + 0.5 * (pts[-1][j] - cen[j]) for j in range(n)]
            fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                for i in range(1, n + 1):
                    pts[i] = [pts[0][j] + 0.5 * (pts[i][j] - pts[0][j]) for j in range(n)]
                    vals[i] = f(pts[i])
    i = min(range(n + 1), key=lambda k: vals[k])
    return pts[i], vals[i]
