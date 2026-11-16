"""PyTorch neural-net comparison for the permafrost classifier (plan A2). Optional: needs torch.

fit_predict(X_tr, y_tr, X_te) -> p(ice) for X_te. Same inputs as permafrost.model(): the
permafrost.features() frame (NUMERIC columns + glim_class as float codes, NaN = missing).
Preprocessing is fitted on the training fold only. CPU, fixed seed, no tuning.
"""
import numpy as np

try:
    import torch
    from torch import nn
except ImportError:  # optional dependency: the pipeline never requires torch
    torch = None

CAT = "glim_class"
N_GLIM = 16          # len(permafrost.GLIM_CODES); embedding index 0 = missing / unseen
EPOCHS, PATIENCE, BATCH, VAL_FRAC = 100, 10, 256, 0.15


def available():
    return torch is not None


class Net(nn.Module if torch else object):
    def __init__(self, n_numeric, n_cat=N_GLIM + 1, emb_dim=4):
        super().__init__()
        self.emb = nn.Embedding(n_cat, emb_dim)
        self.body = nn.Sequential(
            nn.Linear(n_numeric + emb_dim, 128), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(128, 64), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(64, 1))

    def forward(self, x_num, x_cat):
        return self.body(torch.cat([x_num, self.emb(x_cat)], dim=1)).squeeze(1)


def _prep(X, stats=None):
    """Numeric: median-impute + standardize (stats from training fold). Cat: code+1, 0 = missing."""
    num = X.drop(columns=[CAT], errors="ignore").to_numpy(float)
    if stats is None:
        med = np.nanmedian(num, axis=0)
        med = np.where(np.isnan(med), 0.0, med)              # all-missing column -> 0
        filled = np.where(np.isnan(num), med, num)
        std = filled.std(axis=0)
        stats = (med, filled.mean(axis=0), np.where(std > 0, std, 1.0))
    med, mu, sd = stats
    num = (np.where(np.isnan(num), med, num) - mu) / sd
    cat = X[CAT].to_numpy(float) if CAT in X else np.full(len(X), np.nan)
    cat = np.where(np.isnan(cat) | (cat < 0) | (cat >= N_GLIM), -1, cat).astype(int) + 1
    return (torch.tensor(num, dtype=torch.float32), torch.tensor(cat, dtype=torch.long)), stats


def fit_predict(X_tr, y_tr, X_te, seed=0):
    """Train on (X_tr, y_tr), return p(ice) for X_te. Early stopping on a split of the TRAINING fold."""
    if torch is None:
        raise ImportError("torch is not installed")
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    y_tr = np.asarray(y_tr, float)
    (xn, xc), stats = _prep(X_tr)
    (tn, tc), _ = _prep(X_te, stats)
    y = torch.tensor(y_tr, dtype=torch.float32)

    idx = rng.permutation(len(y))
    n_val = max(1, int(len(y) * VAL_FRAC)) if len(y) >= 20 else 0
    val, tr = idx[:n_val], idx[n_val:]

    pos = float(y_tr[tr].sum())
    neg = float(len(tr) - pos)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(neg / pos if pos > 0 else 1.0))
    net = Net(xn.shape[1])
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)

    best, best_state, waited = np.inf, None, 0
    for _ in range(EPOCHS):
        net.train()
        for b in np.array_split(rng.permutation(tr), max(1, len(tr) // BATCH)):
            opt.zero_grad()
            loss_fn(net(xn[b], xc[b]), y[b]).backward()
            opt.step()
        if not n_val:
            continue
        net.eval()
        with torch.no_grad():
            v = loss_fn(net(xn[val], xc[val]), y[val]).item()
        if v < best - 1e-4:
            best, waited = v, 0
            best_state = {k: t.clone() for k, t in net.state_dict().items()}
        else:
            waited += 1
            if waited >= PATIENCE:
                break
    if best_state is not None:
        net.load_state_dict(best_state)
    net.eval()
    with torch.no_grad():
        return torch.sigmoid(net(tn, tc)).numpy().astype(float)
