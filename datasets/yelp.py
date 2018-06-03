import os

import numpy as np
import pandas as pd
from torchnlp.datasets.dataset import Dataset
from torchnlp.download import download_file_maybe_extract

from .data_utils import text_preprocess


def yelp_dataset(directory='data/', train=False, test=False, check_files=['readme.txt'],
                 # yelp_review_full, yelp_review_polarity
                 urls=['https://drive.google.com/uc?export=download&id=1Ve7f-s7Cv1R77vBtmEIBbXaAtjHrigNO',
                       'https://drive.google.com/uc?export=download&id=1p2av1gm_0GqP8MYwDibNSto384PhxR3P'],
                 fine_grained=False):
    """
    Load the Yelp Review Full Star or Yelp Review Polarity dataset (Version 1).

    The Yelp reviews full star dataset is constructed by randomly taking 130,000 training samples and
    10,000 testing samples for each review star from 1 to 5. In total there are 650,000 trainig samples
    and 50,000 testing samples.
    The Yelp reviews polarity dataset is constructed by considering stars 1 and 2 negative, and 3 and 4
    positive. For each polarity 280,000 training samples and 19,000 testing samples are take randomly.
    In total there are 560,000 trainig samples and 38,000 testing samples. Negative polarity is class 1,
    and positive class 2.

    **Reference:** http://www.yelp.com/dataset_challenge

    Args:
        directory (str, optional): Directory to cache the dataset.
        train (bool, optional): If to load the training split of the dataset.
        test (bool, optional): If to load the test split of the dataset.
        check_files (str, optional): Check if these files exist, then this download was successful.
        urls (str, optional): URLs to download.
        fine_grained (bool, optional): Whether to use 5-class instead of 2-class labeling. Which means using
        yelp_review_full dataset instead of yelp_review_polarity dataset

    Returns:
        :class:`tuple` of :class:`torchnlp.datasets.Dataset`: Tuple with the training dataset and
        test dataset in order if their respective boolean argument is true.

    Example:
        >>> from datasets import yelp_dataset
        >>> train = yelp_dataset(train=True)
        >>> train[0:2]
        [{
          'label': '1',
          'text': "Unfortunately, the frustration of being Dr. Goldberg's patient is..."}
         {
          'label': '2',
          'text': "Been going to Dr. Goldberg for over 10 years. I think I was one of..."}]
    """
    if fine_grained:
        extracted_name, url = 'yelp_review_full', urls[0]
    else:
        extracted_name, url = 'yelp_review_polarity', urls[1]
    for i in range(len(check_files)):
        check_files[i] = os.path.join(extracted_name, check_files[i])
    download_file_maybe_extract(url=url, directory=directory, filename=extracted_name + '.tar.gz',
                                check_files=check_files)

    ret = []
    splits = [file_name for (requested, file_name) in [(train, 'train.csv'), (test, 'test.csv')] if requested]
    for file_name in splits:
        csv_file = np.array(pd.read_csv(os.path.join(directory, extracted_name, file_name), header=None)).tolist()
        examples = []
        for data in csv_file:
            label, text = str(data[0]), data[1]
            text = text_preprocess(text)
            examples.append({'label': label, 'text': text})
        ret.append(Dataset(examples))

    if len(ret) == 1:
        return ret[0]
    else:
        return tuple(ret)
