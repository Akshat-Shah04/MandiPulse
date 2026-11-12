"""
Step 3 of forecasting: the models.

Every model has the same two methods so we can test them the same way:

    model.train(examples)            learn from examples that have answers
    model.predict(examples)          -> numpy array, one row of 14 numbers per example

Each predicted number is log(future price / today's price), like the answers.
"""

import numpy as np
import torch
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from torch import nn

from .examples import FORECAST_DAYS, HISTORY_DAYS, answer_column, feature_rows, has_all_answers

RANDOM_SEED = 7  # fixed so results are the same every run


# ---------------------------------------------------------------- 1. naive


class NaiveModel:
    """'The price will stay what it is today.' Simple, but surprisingly hard to beat."""

    name = "naive"

    def train(self, examples):
        pass  # nothing to learn

    def predict(self, examples):
        return np.zeros((len(examples), FORECAST_DAYS))  # log(1) = 0 = "no change"


# ---------------------------------------------------------------- 2. seasonal naive


class SameWeekdayModel:
    """'The price will be what it was on the same weekday last week.'"""

    name = "seasonal_naive"

    def train(self, examples):
        pass

    def predict(self, examples):
        result = np.zeros((len(examples), FORECAST_DAYS))
        for i, ex in enumerate(examples):
            for h in range(1, FORECAST_DAYS + 1):
                # Target day is T+h. One week before that is T+h-7 (or T+h-14
                # for the second week), which is inside our 28-day history.
                days_back = 7 - h if h <= 7 else 14 - h
                result[i, h - 1] = ex.history[-1 - days_back]
        return result


# ---------------------------------------------------------------- 3 & 4. ridge and random forest


class FeatureModel:
    """Wraps any scikit-learn regressor that works on our summary features."""

    def __init__(self, name, regressor, scale=False):
        self.name = name
        self.regressor = regressor
        self.scaler = StandardScaler() if scale else None
        self.commodity_names = []

    def train(self, examples):
        examples = [ex for ex in examples if has_all_answers(ex)]
        self.commodity_names = sorted({ex.commodity for ex in examples})
        X = feature_rows(examples, self.commodity_names)
        y = answer_column(examples)
        if self.scaler:
            X = self.scaler.fit_transform(X)
        self.regressor.fit(X, y)

    def predict(self, examples):
        X = feature_rows(examples, self.commodity_names)
        if self.scaler:
            X = self.scaler.transform(X)
        flat = self.regressor.predict(X)
        return flat.reshape(len(examples), FORECAST_DAYS)  # back to one row per example


def make_ridge_model():
    # Linear regression with a small penalty so no single feature dominates.
    return FeatureModel("ridge", Ridge(alpha=3.0), scale=True)


def make_random_forest_model():
    forest = RandomForestRegressor(
        n_estimators=300,  # number of trees
        min_samples_leaf=25,  # each leaf must cover 25 examples -> less overfitting
        max_features=0.5,  # each split looks at half the features -> more varied trees
        n_jobs=-1,  # use every CPU core
        random_state=RANDOM_SEED,
    )
    return FeatureModel("random_forest", forest)


# ---------------------------------------------------------------- 5. LSTM


class PriceLSTM(nn.Module):
    """
    Reads the last 28 days one by one, then predicts the next 14 days.

    Input for each day: [relative log price, sin(weekday), cos(weekday)]
    The commodity is added after the LSTM as a small learned vector
    ("embedding"), so the network can tell onions from wheat.
    """

    def __init__(self, number_of_commodities, hidden_size=48):
        super().__init__()
        self.lstm = nn.LSTM(input_size=3, hidden_size=hidden_size, batch_first=True)
        self.commodity_vector = nn.Embedding(number_of_commodities, 4)
        self.output = nn.Sequential(
            nn.Linear(hidden_size + 4, 64),
            nn.ReLU(),
            nn.Linear(64, FORECAST_DAYS),
        )

    def forward(self, days, commodity):
        _, (last_hidden, _) = self.lstm(days)  # last_hidden: summary of all 28 days
        combined = torch.cat([last_hidden[-1], self.commodity_vector(commodity)], dim=1)
        return self.output(combined) / 10  # undo the x10 scaling of the inputs


class LSTMModel:
    name = "lstm"

    def __init__(self, max_epochs=40, batch_size=256, learning_rate=0.002):
        self.max_epochs = max_epochs
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.network = None
        self.commodity_names = []

    def to_tensors(self, examples):
        """Turn examples into the two tensors the network expects."""
        days = []
        commodities = []
        for ex in examples:
            sequence = []
            for i, value in enumerate(ex.history):
                # weekday of history day i (the last one, i = 27, is day T)
                weekday = (ex.day.weekday() - (HISTORY_DAYS - 1 - i)) % 7
                angle = 2 * np.pi * weekday / 7
                # x10 makes the small log changes a friendlier size for the network
                sequence.append([value * 10, np.sin(angle), np.cos(angle)])
            days.append(sequence)
            commodities.append(self.commodity_names.index(ex.commodity))
        return torch.tensor(days, dtype=torch.float32), torch.tensor(commodities, dtype=torch.long)

    def train(self, examples):
        torch.manual_seed(RANDOM_SEED)
        examples = [ex for ex in examples if has_all_answers(ex)]
        examples.sort(key=lambda ex: ex.day)
        self.commodity_names = sorted({ex.commodity for ex in examples})

        # Keep the newest 10% aside. After each epoch we check the error on
        # them and remember the best version of the network (early stopping).
        split = int(len(examples) * 0.9)
        train_part, check_part = examples[:split], examples[split:]

        x_train, c_train = self.to_tensors(train_part)
        y_train = torch.tensor([ex.answers for ex in train_part], dtype=torch.float32)
        x_check, c_check = self.to_tensors(check_part)
        y_check = torch.tensor([ex.answers for ex in check_part], dtype=torch.float32)

        self.network = PriceLSTM(len(self.commodity_names))
        optimizer = torch.optim.Adam(self.network.parameters(), lr=self.learning_rate)
        loss_function = nn.HuberLoss(delta=0.1)  # like squared error, but calmer about outliers

        best_loss = float("inf")
        best_weights = None
        epochs_without_improvement = 0

        for epoch in range(self.max_epochs):
            # --- learn from the training part in small batches
            self.network.train()
            shuffled = torch.randperm(len(train_part))
            for start in range(0, len(train_part), self.batch_size):
                batch = shuffled[start: start + self.batch_size]
                optimizer.zero_grad()
                prediction = self.network(x_train[batch], c_train[batch])
                loss = loss_function(prediction, y_train[batch])
                loss.backward()
                optimizer.step()

            # --- see how we do on the held-back newest examples
            self.network.eval()
            with torch.no_grad():
                check_loss = loss_function(self.network(x_check, c_check), y_check).item()

            if check_loss < best_loss:
                best_loss = check_loss
                best_weights = {k: v.clone() for k, v in self.network.state_dict().items()}
                epochs_without_improvement = 0
            else:
                epochs_without_improvement += 1
                if epochs_without_improvement >= 6:
                    break  # it stopped getting better; no point training longer

        self.network.load_state_dict(best_weights)

    def predict(self, examples):
        x, c = self.to_tensors(examples)
        self.network.eval()
        with torch.no_grad():
            return self.network(x, c).numpy()


def all_models():
    return [NaiveModel(), SameWeekdayModel(), make_ridge_model(), make_random_forest_model(), LSTMModel()]
