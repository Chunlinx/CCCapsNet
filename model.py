import math

import torch
import torch.nn.functional as F
from capsule_layer import CapsuleLinear
from torch import nn
from torch.nn.parameter import Parameter


class CompositionalEmbedding(nn.Module):
    r"""A simple compositional codeword and codebook that store embeddings.

     Args:
        num_embeddings (int): size of the dictionary of embeddings
        embedding_dim (int): size of each embedding vector
        num_codebook (int): size of the codebook of embeddings
        num_codeword (int, optional): size of the codeword of embeddings
        weighted (bool, optional): weighted version of unweighted version

     Shape:
         - Input: (LongTensor): (N, W), W = number of indices to extract per mini-batch
         - Output: (Tensor): (N, W, embedding_dim)

     Attributes:
         - code (Tensor): the learnable weights of the module of shape
              (num_embeddings, num_codebook, num_codeword)
         - codebook (Tensor): the learnable weights of the module of shape
              (num_codebook, num_codeword, embedding_dim)

     Examples::
         >>> m = CompositionalEmbedding(200, 64, 16, 32)
         >>> a = torch.randperm(128).view(16, -1)
         >>> output = m(a)
         >>> print(output.size())
         torch.Size([16, 8, 64])
     """

    def __init__(self, num_embeddings, embedding_dim, num_codebook, num_codeword=None, weighted=True):
        super(CompositionalEmbedding, self).__init__()
        self.num_embeddings = num_embeddings
        self.embedding_dim = embedding_dim
        self.num_codebook = num_codebook
        self.weighted = weighted

        if num_codeword is None:
            num_codeword = math.ceil(math.pow(num_embeddings, 1 / num_codebook))
        self.code = Parameter(torch.Tensor(num_embeddings, num_codebook, num_codeword))
        self.codebook = Parameter(torch.Tensor(num_codebook, num_codeword, embedding_dim))

        nn.init.normal_(self.code)
        nn.init.normal_(self.codebook)

    def forward(self, input, iteration=10):
        batch_size = input.size(0)
        index = input.view(-1)
        code = self.code.index_select(dim=0, index=index)
        if self.weighted:
            # reweight, do softmax, make sure the sum of weight about each book to 1
            code = F.softmax(code, dim=-2)
            out = (code[:, :, None, :] @ self.codebook[None, :, :, :]).squeeze(dim=-2).sum(dim=1)
        else:
            # because Gumbel SoftMax works in a stochastic manner, needs to run several times to
            # get more accurate embedding
            code = (torch.sum(torch.stack([F.gumbel_softmax(code) for _ in range(iteration)]), dim=0)).argmax(dim=-1)
            out = []
            for index in range(self.num_codebook):
                out.append(self.codebook[index, :, :].index_select(dim=0, index=code[:, index].view(-1)))
            out = torch.sum(torch.stack(out), dim=0)

        out = out.view(batch_size, -1, self.embedding_dim)
        return out

    def __repr__(self):
        return self.__class__.__name__ + ' (' + str(self.num_embeddings) + ', ' + str(self.embedding_dim) + ')'


class Model(nn.Module):
    def __init__(self, vocab_size, embedding_size, num_codebook, num_codeword, hidden_size, in_length, out_length,
                 num_class, routing_type, embedding_type, classifier_type, num_iterations):
        super().__init__()

        self.in_length, self.out_length = in_length, out_length
        self.hidden_size, self.classifier_type = hidden_size, classifier_type

        if embedding_type == 'cwc':
            self.embedding = CompositionalEmbedding(vocab_size, embedding_size, num_codebook, num_codeword,
                                                    weighted=True)
        elif embedding_type == 'cc':
            self.embedding = CompositionalEmbedding(vocab_size, embedding_size, num_codebook, num_codeword,
                                                    weighted=False)
        else:
            self.embedding = nn.Embedding(vocab_size, embedding_size)
        self.features = nn.GRU(embedding_size, self.hidden_size, num_layers=2, dropout=0.5, batch_first=True,
                               bidirectional=True)
        if classifier_type == 'capsule' and routing_type == 'k_means':
            self.classifier = CapsuleLinear(out_capsules=num_class, in_length=self.in_length,
                                            out_length=self.out_length, in_capsules=self.hidden_size // self.in_length,
                                            share_weight=False, routing_type='k_means', num_iterations=num_iterations,
                                            bias=False)
        elif classifier_type == 'capsule' and routing_type == 'dynamic':
            self.classifier = CapsuleLinear(out_capsules=num_class, in_length=self.in_length,
                                            out_length=self.out_length, in_capsules=self.hidden_size // self.in_length,
                                            share_weight=False, routing_type='dynamic', num_iterations=num_iterations,
                                            bias=False)
        else:
            self.classifier = nn.Linear(in_features=self.hidden_size, out_features=num_class, bias=False)

    def forward(self, x):
        embed = self.embedding(x)
        out, _ = self.features(embed)

        out = out[:, :, :self.hidden_size] + out[:, :, self.hidden_size:]
        out = out.mean(dim=1).contiguous()
        if self.classifier_type == 'capsule':
            out = out.view(out.size(0), -1, self.in_length)
            out = self.classifier(out)
            classes = out.norm(dim=-1)
        else:
            out = out.view(out.size(0), -1)
            classes = self.classifier(out)
        return classes
