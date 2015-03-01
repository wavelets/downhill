# -*- coding: utf-8 -*-

'''This module defines gradient descent optimizers with adaptive learning rates.
'''

import climate
import numpy as np
import theano.tensor as TT

from .base import as_float, shared_like
from .first_order import SGD

logging = climate.get_logger(__name__)


class RProp(SGD):
    r'''Trainer for neural nets using resilient backpropagation.

    The RProp method uses the same general strategy as SGD (both methods are
    make small parameter adjustments using local derivative information). The
    difference is that in RProp, only the signs of the partial derivatives are
    taken into account when making parameter updates. That is, the step size for
    each parameter is independent of the magnitude of the gradient for that
    parameter.

    To accomplish this, RProp maintains a separate learning rate for every
    parameter in the model, and adjusts this learning rate based on the
    consistency of the sign of the gradient of the loss with respect to that
    parameter over time. Whenever two consecutive gradients for a parameter have
    the same sign, the learning rate for that parameter increases, and whenever
    the signs disagree, the learning rate decreases. This has a similar effect
    to momentum-based SGD methods but effectively maintains parameter-specific
    learning rates.

    .. math::
        \begin{eqnarray*}
        && \mbox{if } \frac{\partial\mathcal{L}}{\partial p}_{t-1}\frac{\partial\mathcal{L}}{\partial p} > 0 \\
        && \qquad \Delta_t = \min (\eta_+\Delta_{t−1}, \Delta_+) \\
        && \mbox{if } \frac{\partial\mathcal{L}}{\partial p}_{t-1}\frac{\partial\mathcal{L}}{\partial p} < 0 \\
        && \qquad \Delta_t = \max (\eta_-\Delta_{t−1}, \Delta_-) \\
        && \qquad \frac{\partial\mathcal{L}}{\partial p} = 0 \\
        && p_{t+1} = p_t − \mbox{sgn}\left(\frac{\partial\mathcal{L}}{\partial p}\right) \Delta_t
        \end{eqnarray*}

    Here, :math:`s(\cdot)` is the sign function (i.e., returns -1 if its
    argument is negative and 1 otherwise), :math:`\eta_-` and :math:`\eta_+` are
    the amount to decrease (increase) the step size if the gradients disagree
    (agree) in sign, and :math:`\Delta_+` and :math:`\Delta_-` are the maximum
    and minimum step size.

    The implementation here is actually the "iRprop-" variant of RProp described
    in Algorithm 4 from Igel and Huesken, "Improving the Rprop Learning
    Algorithm" (2000). This variant resets the running gradient estimates to
    zero in cases where the previous and current gradients have switched signs.
    '''

    def prepare(self, **kwargs):
        self.step_increase = as_float(kwargs.get('rprop_increase', 1.01))
        self.step_decrease = as_float(kwargs.get('rprop_decrease', 0.99))
        self.min_step = as_float(kwargs.get('rprop_min_step', 0.))
        self.max_step = as_float(kwargs.get('rprop_max_step', 100.))
        super(RProp, self).prepare(*args, **kwargs)

    def updates_for(self, param, grad):
        grad_tm1 = shared_like(param, 'grad')
        step_tm1 = shared_like(param, 'step', self.learning_rate.value)
        test = grad * grad_tm1
        same = TT.gt(test, 0)
        diff = TT.lt(test, 0)
        step = TT.minimum(self.max_step, TT.maximum(self.min_step, step_tm1 * (
            TT.eq(test, 0) +
            same * self.step_increase +
            diff * self.step_decrease)))
        grad = grad - diff * grad
        yield param, param - TT.sgn(grad) * step
        yield grad_tm1, grad
        yield step_tm1, step


class RMSProp(SGD):
    r'''RMSProp trains neural network models using scaled SGD.

    The RMSProp method uses the same general strategy as SGD, in the sense that
    all gradient-based methods make small parameter adjustments using local
    derivative information. The difference here is that as gradients are
    computed during each parameter update, an exponential moving average of
    gradient magnitudes is maintained as well. At each update, the EWMA is used
    to compute the root-mean-square (RMS) gradient value that's been seen in the
    recent past. The actual gradient is normalized by this RMS scaling factor
    before being applied to update the parameters.

    .. math::
        \begin{eqnarray*}
        f_{t+1} &=& \gamma a_t + (1 - \gamma) \frac{\partial\mathcal{L}}{\partial p} \\
        g_{t+1} &=& \gamma g_t + (1 - \gamma) \left(\frac{\partial\mathcal{L}}{\partial p}\right)^2 \\
        v_{t+1} &=& \mu v_t - \frac{\alpha}{\sqrt{g_{t+1} - f_{t+1}^2 + \epsilon}} \frac{\partial\mathcal{L}}{\partial p} \\
        p_{t+1} &=& p_t + v_{t+1}
        \end{eqnarray*}

    Like :class:`Rprop`, this learning method effectively maintains a sort of
    parameter-specific momentum value, but this method takes into account both
    the sign and the magnitude of the gradient for each parameter.

    In this implementation, :math:`\epsilon = 1e-4`, and the weight parameter
    :math:`\gamma` for the EWMA window is computed from the ``rms_halflife``
    keyword argument, such that the actual EWMA weight varies inversely with the
    halflife :math:`h`: :math:`\gamma = e^{\frac{-\ln 2}{h}}`.

    The implementation here is taken from Graves, "Generating Sequences With
    Recurrent Neural Networks" (2013), equations (38)--(45); the paper is
    available at http://arxiv.org/abs/1308.0850. Graves' implementation in
    particular seems to have introduced the :math:`f_t` terms into the RMS
    computation; these terms appear to act as a sort of momentum for the RMS
    values.
    '''

    def prepare(self, **kwargs):
        self.ewma = as_float(np.exp(-np.log(2) / kwargs.get('rms_halflife', 7)))
        super(RMSProp, self).prepare(**kwargs)

    def updates_for(self, param, grad):
        eps = 1e-4
        g1_tm1 = shared_like(param, 'g1_ewma')
        g2_tm1 = shared_like(param, 'g2_ewma')
        vel_tm1 = shared_like(param, 'vel')
        g1_t = self.ewma * g1_tm1 + (1 - self.ewma) * grad
        g2_t = self.ewma * g2_tm1 + (1 - self.ewma) * grad * grad
        rms = TT.sqrt(g2_t - g1_t * g1_t + eps)
        vel_t = self.momentum * vel_tm1 - grad * self.learning_rate / rms
        yield g1_tm1, g1_t
        yield g2_tm1, g2_t
        yield vel_tm1, vel_t
        yield param, param + vel_t


class ADADELTA(RMSProp):
    r'''ADADELTA trains neural network models using scaled :class:`SGD`.

    The ADADELTA method uses the same general strategy as :class:`SGD` (both
    methods are make small parameter adjustments using local derivative
    information). The difference here is that as gradients are computed during
    each parameter update, an exponential weighted moving average gradient
    value, as well as an exponential weighted moving average of recent parameter
    steps, are maintained as well. The actual gradient is normalized by the
    ratio of the parameter step RMS values to the gradient RMS values.

    .. math::
        \begin{eqnarray*}
        g_{t+1} &=& \gamma g_t + (1 - \gamma) \left(\frac{\partial\mathcal{L}}{\partial p}\right)^2 \\
        v_{t+1} &=& -\frac{\sqrt{x_t + \epsilon}}{\sqrt{g_{t+1} + \epsilon}} \frac{\partial\mathcal{L}}{\partial p} \\
        x_{t+1} &=& \gamma x_t + (1 - \gamma) v_{t+1}^2 \\
        p_{t+1} &=& p_t + v_{t+1}
        \end{eqnarray*}

    Like :class:`Rprop` and the :class:`RMSProp`--:class:`ESGD` family, this
    learning method effectively maintains a sort of parameter-specific momentum
    value. The primary difference between this method and :class:`RMSProp` is
    that ADADELTA additionally incorporates a sliding window of RMS parameter
    steps, obviating the need for a learning rate parameter.

    In this implementation, :math:`\epsilon` is set to 1e-4. The weight
    parameter :math:`\gamma` for the EWMA window is computed from the
    ``rms_halflife`` keyword argument, such that the actual EWMA weight varies
    inversely with the halflife :math:`h`: :math:`\gamma = e^{\frac{-\ln
    2}{h}}`.

    The implementation is modeled after Zeiler (2012), "ADADELTA: An adaptive
    learning rate method," available at http://arxiv.org/abs/1212.5701.
    '''

    def updates_for(self, param, grad):
        eps = 1e-4
        x2_tm1 = shared_like(param, 'x2_ewma')
        g2_tm1 = shared_like(param, 'g2_ewma')
        g2_t = self.ewma * g2_tm1 + (1 - self.ewma) * grad * grad
        delta = grad * TT.sqrt(x2_tm1 + eps) / TT.sqrt(g2_t + eps)
        x2_t = self.ewma * x2_tm1 + (1 - self.ewma) * delta * delta
        yield g2_tm1, g2_t
        yield x2_tm1, x2_t
        yield param, param - delta


class ESGD(RMSProp):
    r'''Equilibrated SGD computes a diagonal preconditioner for gradient descent.

    The ESGD method uses the same general strategy as SGD, in the sense that all
    gradient-based methods make small parameter adjustments using local
    derivative information. The difference here is that as gradients are
    computed during each parameter update, an exponential moving average of
    diagonal preconditioner values is maintained as well. At each update, the
    EWMA is used to compute the root-mean-square (RMS) diagonal preconditioner
    value that's been seen in the recent past. The actual gradient is normalized
    by this preconditioner before being applied to update the parameters.

    .. math::
        \begin{eqnarray*}
        r &\sim& \mathcal{N}(0, 1) \\
        Hr &=& \frac{\partial^2 \mathcal{L}}{\partial^2 p}r \\
        D_{t+1} &=& \gamma D_t + (1 - \gamma) (Hr)^2 \\
        v_{t+1} &=& \mu v_t - \frac{\alpha}{\sqrt{D_{t+1} + \epsilon}} \frac{\partial\mathcal{L}}{\partial p} \\
        p_{t+1} &=& p_t + v_{t+1}
        \end{eqnarray*}

    Like :class:`Rprop` and the :class:`ADADELTA`--:class:`RMSProp` family, this
    learning method effectively maintains a sort of parameter-specific momentum
    value. The primary difference between this method and :class:`RMSProp` is
    that ESGD treats the normalizing fraction explicitly as a preconditioner for
    the diaonal of the Hessian, and estimates this diagonal by drawing a vector
    of standard normal values at every training step. The primary difference
    between this implementation and the algorithm described in the paper (see
    below) is the use of an EWMA to decay the diagonal values over time, while
    in the paper the diagonal is divided by the training iteration.

    In this implementation, :math:`\epsilon` is set to 1e-4. The weight
    parameter :math:`\gamma` for the EWMA window is computed from the
    ``rms_halflife`` keyword argument, such that the actual EWMA weight varies
    inversely with the halflife :math:`h`: :math:`\gamma = e^{\frac{-\ln
    2}{h}}`.

    The implementation here is modeled after Dauphin, de Vries, Chung & Bengio
    (2014), "RMSProp and equilibrated adaptive learning rates for non-convex
    optimization," http://arxiv.org/pdf/1502.04390.pdf.
    '''

    def __init__(self, *args, **kwargs):
        self.rng = RandomStreams()
        super(ESGD, self).__init__(*args, **kwargs)

    def updates_for(self, param, grad):
        eps = 1e-4  # more or less from the paper
        D_tm1 = shared_like(param, 'D_ewma')
        vel_tm1 = shared_like(param, 'vel')
        Hv = TT.Rop(grad, param, self.rng.normal(param.shape))
        D_t = self.ewma * D_tm1 + (1 - self.ewma) * Hv * Hv
        vel_t = self.momentum * vel_tm1 - grad * self.learning_rate / TT.sqrt(D_t + eps)
        yield D_tm1, D_t
        yield param, param + vel_t