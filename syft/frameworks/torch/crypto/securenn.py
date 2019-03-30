import torch
from typing import Union

import syft
import syft as sy
from syft import AdditiveSharingTensor, MultiPointerTensor


# Q field
Q_BITS = 31
field = (2 ** Q_BITS) - 1

L = field
p = field


def decompose(tensor: Union[AdditiveSharingTensor, MultiPointerTensor]):
    """decompose a tensor into its binary representation."""
    powers = torch.arange(Q_BITS)
    if hasattr(tensor, "child") and isinstance(tensor.child, dict):
        powers = powers.send(*list(tensor.child.keys())).child
    for i in range(len(tensor.shape)):
        powers = powers.unsqueeze(0)
    tensor = tensor.unsqueeze(-1)
    moduli = 2 ** powers
    tensor = torch.fmod(((tensor + 2 ** (Q_BITS)) / moduli.type_as(tensor)), 2)
    return tensor


def flip(x, dim):
    indices = torch.arange(x.shape[dim] - 1, -1, -1).long()

    if hasattr(x, "child") and isinstance(x.child, dict):
        indices = indices.send(*list(x.child.keys())).child

    return x.index_select(dim, indices)


def private_compare(x, r, BETA, j, alice, bob):

    # t = torch.fmod((r + 1), 2 ** l)
    t = torch.fmod((r + 1), field)

    # R_MASK = (r == ((2 ** l) - 1)).long()
    R_MASK = (r == (field - 1)).long()

    assert isinstance(x, AdditiveSharingTensor)

    r = decompose(r)
    t = decompose(t)
    BETA = BETA.unsqueeze(1).expand(list(r.shape))
    R_MASK = R_MASK.unsqueeze(1).expand(list(r.shape))
    u = (torch.rand(x.shape) > 0.5).long().send(bob, alice).child
    l1_mask = torch.zeros(x.shape).long()
    l1_mask[:, -1:] = 1
    l1_mask = l1_mask.send(bob, alice).child

    # if BETA == 0
    assert isinstance(j, MultiPointerTensor)
    assert isinstance(r, MultiPointerTensor)

    w = (j * r) + x - (2 * x * r)

    wf = flip(w, 1)
    wfc = wf.cumsum(1) - wf
    wfcf = flip(wfc, 1)

    c_beta0 = (j * r) - x + j + wfcf

    # elif BETA == 1 AND r != 2**Q_BITS - 1
    w = x + (j * t) - (2 * t * x)  # FIXME: unused
    c_beta1 = (-j * t) + x + j + wfcf

    # else
    c_igt1 = (1 - j) * (u + 1) - (j * u)

    assert isinstance(c_igt1, MultiPointerTensor)

    print("c_igt1", c_igt1)
    c_ie1 = (j * -2) + 1
    print("c_ie1", c_ie1)
    c_21l = (l1_mask * c_ie1) + ((1 - l1_mask) * c_igt1)
    print("c_21l", c_21l)

    print("BETA", BETA)
    c = (BETA * c_beta0) + (1 - BETA) * c_beta1
    c = (c * (1 - R_MASK)) + (c_21l * R_MASK)

    print("c", c)
    cmpc = c.get()  # /2
    result = (cmpc == 0).sum(1)
    return result


def msb(a_sh: syft.AdditiveSharingTensor, alice, bob):
    """
    :param a_sh (AdditiveSharingTensor):
    :param alice:
    :param bob:
    :return:
    """

    crypto_provider = a_sh.crypto_provider

    input_shape = a_sh.shape
    a_sh = a_sh.view(-1)

    print("a_sh", a_sh)

    # the commented out numbers below correspond to the
    # line numbers in Table 5 of the SecureNN paper
    # https://eprint.iacr.org/2018/442.pdf

    # 1)
    x = torch.LongTensor(a_sh.shape).random_(L - 1)
    x_bit = decompose(x)
    x_sh = x.share(bob, alice, crypto_provider=crypto_provider).child
    x_bit_0 = x_bit[..., -1:]  # pretty sure decompose is backwards...
    x_bit_sh_0 = x_bit_0.share(
        bob, alice, crypto_provider=crypto_provider
    ).child  # least -> greatest from left -> right
    x_bit_sh = x_bit.share(bob, alice, crypto_provider=crypto_provider).child

    # 2)
    y_sh = 2 * a_sh

    r_sh = y_sh + x_sh

    # 3)
    r = r_sh.get()  # .send(bob, alice) #TODO: make this secure by exchanging shares remotely
    r_0 = decompose(r)[..., -1].send(bob, alice).child
    r = r.send(bob, alice).child

    assert isinstance(r, MultiPointerTensor)

    j0 = torch.zeros(x_bit_sh.shape).long().send(bob)
    j1 = torch.ones(x_bit_sh.shape).long().send(alice)
    j = syft.MultiPointerTensor(children=[j0, j1])
    j_0 = j[..., -1]

    assert isinstance(j, MultiPointerTensor)
    assert isinstance(j_0, MultiPointerTensor)

    # 4)
    BETA = (torch.rand(a_sh.shape) > 0.5).long().send(bob, alice).child

    assert isinstance(BETA, MultiPointerTensor)
    BETA_prime = private_compare(x_bit_sh, r, BETA=BETA, j=j, alice=alice, bob=bob).long()

    # 5)
    BETA_prime_sh = BETA_prime.share(bob, alice, crypto_provider=crypto_provider).child

    # 7)
    _lambda = BETA_prime_sh + (j_0 * BETA) - (2 * BETA * BETA_prime_sh)  # TODO I rm ADDISHARET

    # 8)
    _delta = x_bit_sh_0.squeeze(-1) + (j_0 * r_0) - (2 * r_0 * x_bit_sh_0.squeeze(-1))

    # 9)
    theta = _lambda * _delta

    # 10)
    u = (
        torch.zeros(list(theta.shape))
        .long()
        .share(alice, bob, crypto_provider=crypto_provider)
        .child
    )
    a = _lambda + _delta - (2 * theta) + u

    return a.view(*list(input_shape))


def relu_deriv(a_sh):
    assert isinstance(a_sh, AdditiveSharingTensor)

    workers = [a_sh.owner.get_worker(w_name) for w_name in list(a_sh.child.keys())]
    return msb(a_sh, *workers)


def relu(a):
    print("a", a)
    ra = relu_deriv(a)
    print("relu_deriv(a", ra)
    return a * ra