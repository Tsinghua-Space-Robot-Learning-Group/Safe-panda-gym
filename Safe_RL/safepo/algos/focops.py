from turtle import pen
import torch
from Safe_RL.safepo.algos.policy_graident import PG
from Safe_RL.safepo.algos.lagrangian_base import Lagrangian

class FOCOPS(PG,Lagrangian):
    def __init__(
            self,
            alg: str = 'focops',
            eta: float = 0.02,
            lam: float = 1.5,
            cost_limit: float = 25.,
            lagrangian_multiplier_init: float = 0.001,
            lambda_lr: float = 0.05,
            lambda_optimizer: str = 'Adam',
            use_lagrangian_penalty: bool = True,
            use_standardized_advantages: bool = True,
            **kwargs
    ):
        PG.__init__(
            self,
            alg=alg,
            cost_limit=cost_limit,
            lagrangian_multiplier_init=lagrangian_multiplier_init,
            lambda_lr=lambda_lr,
            lambda_optimizer=lambda_optimizer,
            use_cost_value_function=True,
            use_kl_early_stopping=True,
            use_lagrangian_penalty=use_lagrangian_penalty,
            use_standardized_advantages=use_standardized_advantages,
            **kwargs
        )

        Lagrangian.__init__(
            self,
            cost_limit=cost_limit,
            use_lagrangian_penalty=use_lagrangian_penalty,
            lagrangian_multiplier_init=lagrangian_multiplier_init,
            lambda_lr=lambda_lr,
            lambda_optimizer=lambda_optimizer
        )
        self.lam = lam
        self.eta = eta

    def algorithm_specific_logs(self):
        super().algorithm_specific_logs()
        # Because focops: nu needs to clip to [0, 2]
        self.logger.log_tabular('LagrangeMultiplier',
                                self.penalty.item())

    def compute_loss_pi(self, data: dict):
        # Policy loss
        dist, _log_p = self.ac.pi(data['obs'], data['act'])
        ratio = torch.exp(_log_p - data['log_p'])

        kl_new_old = torch.distributions.kl.kl_divergence(dist, self.p_dist).mean()
        if self.use_lagrangian_penalty:
            # ensure that lagrange multiplier is positive
            self.penalty = torch.clamp(self.lagrangian_multiplier, 0.0, 2.0)
            loss_pi = (kl_new_old.detach() - (1 / self.lam) * ratio * (data['adv'] - self.penalty.detach() * data['cost_adv'])) * (kl_new_old.detach() <= self.eta).type(torch.float32)
            loss_pi = loss_pi.mean()
            
        # Useful extra info
        approx_kl = (0.5 * (dist.mean - data['act']) ** 2
                     / dist.stddev ** 2).mean().item()
        ent = dist.entropy().mean().item()
        pi_info = dict(kl=approx_kl, ent=ent, ratio=ratio.mean().item())

        return loss_pi, pi_info

    def update(self):
        raw_data = self.buf.get()
        # pre-process data
        data = self.pre_process_data(raw_data)
        # sub-sampling accelerates calculations
        self.fvp_obs = data['obs'][::4]
        # Note that logger already uses MPI statistics across all processes..
        ep_costs = self.logger.get_stats('EpCosts')[0]
        # First update Lagrange multiplier parameter
        self.update_lagrange_multiplier(ep_costs)
        # now update policy and value network
        self.update_policy_net(data=data)
        self.update_value_net(data=data)
        self.update_cost_net(data=data)
        # Update running statistics, e.g. observation standardization
        # Note: observations from are raw outputs from environment
        self.update_running_statistics(raw_data)